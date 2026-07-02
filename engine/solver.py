"""
solver.py -- OR-Tools CP-SAT timetable solver.

Public API:
    build_and_solve(semester, time_limit_seconds, section_map,
                    progress_queue, _skip_constraints) -> dict

Flow:
    1. Load courses, faculty, constraints from MongoDB
    2. Derive section->course and faculty->(section,course) mappings
    3. Create BoolVars for lectures (x1) and 2-slot blocks (x2)
    4. Apply hard + soft constraints (optionally skip groups via _skip_constraints)
    5. Solve (with optional SolutionCallback for live progress)
    6. Extract & return timetable grids, workload summary, infeasibility hints
"""

import time as _time
import queue as _queue_mod
from collections import defaultdict

from ortools.sat.python import cp_model
from db import get_db
from engine.constraints import (
    NUM_DAYS, NUM_SLOTS, VALID_BLOCK_STARTS,
    SLOT_LABEL_TO_IDX, DAY_LABEL_TO_IDX,
    LAB_ROOMS,
    add_no_faculty_clash,
    add_no_section_clash,
    add_weekly_hours,
    add_no_student_gaps,
    add_oe_concurrency,
    add_aec_concurrency,
    add_pg_shared,
    add_maths_locks,
    add_cse_lab_locks,
    add_spread_constraint,
    add_first_slot_constraint,
    add_co_faculty_logic,
    add_max_workload,
    add_lab_room_assignment,
    add_friday_half_day,
    add_morning_first,
    add_no_empty_days,
)


DAYS_LABELS  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SLOTS_LABELS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]

# -----------------------------------------------------------------------
# Default section map (also mirrored in db._DEFAULT_SECTION_MAP)
# -----------------------------------------------------------------------
_SEMESTER_SECTIONS = {
    "1": ["1A", "1B", "1C", "1K"],
    "2": ["2A", "2B", "2C", "2K"],
    "3": ["3A", "3B", "3C", "3D"],
    "4": ["4A", "4B", "4C", "4D"],
    "5": ["5A", "5B", "5C", "5D"],
    "6": ["6A", "6B", "6C", "6D"],
    "7": ["7A", "7B", "7C"],
    "8": ["8A", "8B", "8C"],
}


# -----------------------------------------------------------------------
# Helper: semester string -> sections
# -----------------------------------------------------------------------
def _sections_for_semester(sem_str: str, section_map: dict | None = None) -> list[str]:
    """Map a semester string (from course/faculty data) to a section list.

    Two code paths:
      1. Whole-semester number (e.g. "3") -> all sections for that semester.
      2. Comma/slash-separated specific sections (e.g. "3A", "3A, 3B", "3A/3B").
    Returns [] if the input cannot be matched.

    Parameters
    ----------
    section_map : optional override dict replacing ``_SEMESTER_SECTIONS``
    """
    lookup = section_map if section_map else _SEMESTER_SECTIONS
    s = str(sem_str).strip()

    # 1. Exact match for a whole semester (e.g. "3" -> ["3A", "3B", "3C", "3D"])
    if s in lookup:
        return list(lookup[s])

    # 2. Check if it's a comma/slash-separated list of specific sections
    all_sections = set()
    for secs in lookup.values():
        all_sections.update(secs)

    parsed_sections = []
    parts = [p.strip() for p in s.replace('/', ',').split(',')]
    for part in parts:
        part_upper = part.upper()
        match = next((sec for sec in all_sections if sec.upper() == part_upper), None)
        if match and match not in parsed_sections:
            parsed_sections.append(match)

    return parsed_sections


# -----------------------------------------------------------------------
# Progress callback for live log (#6)
# -----------------------------------------------------------------------
class _TimetableProgressCallback(cp_model.CpSolverSolutionCallback):
    """Push a log line to *progress_queue* each time a new solution is found."""

    def __init__(self, progress_queue: _queue_mod.Queue):
        super().__init__()
        self._q = progress_queue
        self._start = _time.time()
        self._count = 0

    def on_solution_callback(self):
        self._count += 1
        elapsed = round(_time.time() - self._start, 1)
        try:
            obj = self.ObjectiveValue()
            bound = self.BestObjectiveBound()
            gap = abs(obj - bound)
            msg = (
                f"[{elapsed}s] Solution #{self._count}: "
                f"penalty={obj:.0f}  bound={bound:.0f}  gap={gap:.0f}"
            )
        except Exception:
            msg = f"[{elapsed}s] Feasible solution #{self._count} found."
        self._q.put({"time": elapsed, "message": msg})


# -----------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------
def _load_data(semester: str):
    """
    Load all required data from MongoDB.
    semester: "odd" or "even"
    Returns a dict with courses, faculty, constraints, and derived mappings.
    """
    db = get_db()
    faculty_col = f"faculty_{semester}"

    # --- Courses ---
    courses_raw = list(db["courses"].find({}, {"_id": 0}))
    course_info = {}  # code -> {L, T, P, semester, ...}
    for c in courses_raw:
        code = c.get("course_code", "")
        course_info[code] = {
            "L": int(c.get("L", 0)),
            "T": int(c.get("T", 0)),
            "P": int(c.get("P", 0)),
            "semester": str(c.get("semester", "")),
            "course_name": c.get("course_name", code),
            "lecture_in_lab": c.get("lecture_in_lab", "No"),
            "tutorial_in_lab": c.get("tutorial_in_lab", "No"),
            "elective": c.get("elective", "No"),
            "ug_pg": c.get("ug_pg", "UG"),
            "aec": c.get("aec", "No"),
        }

    # --- Faculty ---
    faculty_raw = list(db[faculty_col].find({}, {"_id": 0}))

    # --- Constraints ---
    constraints_doc = db["constraints"].find_one({"type": "special_subjects"}) or {}

    return {
        "course_info": course_info,
        "faculty_raw": faculty_raw,
        "constraints_doc": constraints_doc,
    }


def _build_mappings(course_info, faculty_raw, constraints_doc, section_map=None):
    """
    Derive:
        section_courses   : {section -> [course_codes]}
        faculty_assignments: {faculty_name -> [(section, course_code)]}
        oe_codes, aec_codes, pg shared info, maths_slots, lab_alloc

    Parameters
    ----------
    section_map : optional dict to override ``_SEMESTER_SECTIONS``
    """
    def _sec_for(sem_str):
        return _sections_for_semester(sem_str, section_map)

    # --- Extract PG common codes first to use in section assignment ---
    pg_core_code = constraints_doc.get("pg_shared_core", "None")
    if pg_core_code == "None": pg_core_code = None
    pg_pe_codes = constraints_doc.get("pg_shared_pe", [])

    # --- section -> course list ---
    section_courses: dict[str, list[str]] = {}
    for code, info in course_info.items():
        sem = info.get("semester", "")
        ug_pg = str(info.get("ug_pg", "UG")).strip().upper()

        if ug_pg == "PG":
            if sem not in ("1", "2"):
                continue
            _ordinal = {"1": "1st", "2": "2nd"}
            label = f"PG {_ordinal[sem]} Sem"
            sections = []
            is_common = (code in pg_pe_codes) or (code == pg_core_code)
            if "MCS" in code or is_common:
                sections.append(f"{label} - SP1")
            if "MCN" in code or is_common:
                sections.append(f"{label} - SP2")
        else:
            sections = _sec_for(sem)

        for sec in sections:
            section_courses.setdefault(sec, []).append(code)

    # --- Maths: add a virtual "MATHS" course for sections that have maths locks ---
    maths_slots = constraints_doc.get("maths_slots", [])
    maths_sections = set()
    for entry in maths_slots:
        cls = entry.get("Class", "")
        if cls:
            maths_sections.add(cls)
    for sec in maths_sections:
        if "MATHS" not in section_courses.get(sec, []):
            section_courses.setdefault(sec, []).append("MATHS")
    if maths_sections:
        maths_per_section_L: dict[str, int] = {}
        maths_per_section_T: dict[str, int] = {}
        for entry in maths_slots:
            cls = entry.get("Class", "")
            fac = entry.get("Faculty", "")
            if cls:
                if "TUT" in fac.upper():
                    maths_per_section_T[cls] = maths_per_section_T.get(cls, 0) + 1
                else:
                    maths_per_section_L[cls] = maths_per_section_L.get(cls, 0) + 1
        max_maths_L = max(maths_per_section_L.values()) if maths_per_section_L else 0
        max_maths_T = max(maths_per_section_T.values()) if maths_per_section_T else 0
        course_info["MATHS"] = {
            "L": max_maths_L, "T": max_maths_T, "P": 0,
            "semester": "", "course_name": "Mathematics",
            "lecture_in_lab": "No", "tutorial_in_lab": "No",
            "elective": "No",
        }

    # --- faculty -> (section, course) assignments ---
    faculty_by_course: dict[str, list[str]] = {}
    faculty_all_courses: dict[str, list[dict]] = {}
    faculty_designations: dict[str, str] = {}
    for fac in faculty_raw:
        name = fac.get("name", "")
        faculty_designations[name] = str(fac.get("designation", "Assistant")).strip()
        for subj in fac.get("subjects", []):
            code = subj.get("code", "")
            sem  = subj.get("semester", "")
            key  = f"{code}|{sem}"
            faculty_by_course.setdefault(key, []).append(name)
            faculty_all_courses.setdefault(name, []).append(
                {"code": code, "semester": sem, "type": "subject"}
            )
        for lab in fac.get("labs", []):
            code = lab.get("code", "")
            sem  = lab.get("semester", "")
            key  = f"{code}|{sem}"
            faculty_by_course.setdefault(key, []).append(name)
            faculty_all_courses.setdefault(name, []).append(
                {"code": code, "semester": sem, "type": "lab"}
            )

    faculty_assignments: dict[str, list[tuple[str, str]]] = {}
    assigned_sections: dict[str, set[str]] = {}

    for key_str, fac_list in faculty_by_course.items():
        code, sem = key_str.split("|", 1)
        sections = _sec_for(sem)
        if not sections:
            continue
        assigned_sections.setdefault(key_str, set())
        for i, fac_name in enumerate(fac_list):
            if i < len(sections):
                sec = sections[i]
                faculty_assignments.setdefault(fac_name, []).append((sec, code))
                assigned_sections[key_str].add(sec)

        remaining = [s for s in sections if s not in assigned_sections[key_str]]
        if remaining and fac_list:
            for j, sec in enumerate(remaining):
                fac_name = fac_list[j % len(fac_list)]
                faculty_assignments.setdefault(fac_name, []).append((sec, code))

    # --- OE / AEC / PG codes ---
    name_to_code = {}
    for code, info in course_info.items():
        name_to_code[info.get("course_name", "")] = code

    oe_names  = constraints_doc.get("open_electives", [])
    oe_codes  = set(name_to_code.get(n, n) for n in oe_names)
    aec_names = constraints_doc.get("aec", [])
    aec_codes = set(name_to_code.get(n, n) for n in aec_names)

    # --- Group Parallel Electives into a Single Variable ---
    def group_parallel_electives(elective_codes, prefix_label):
        sem_groups = {}
        for code in elective_codes:
            if code in course_info:
                sem = course_info[code].get("semester", "")
                if sem:
                    sem_groups.setdefault(sem, []).append(code)

        new_codes = set()
        for sem, codes in sem_groups.items():
            if len(codes) > 1:
                pseudo_code = f"CS{prefix_label}_SEM{sem}"
                new_codes.add(pseudo_code)
                first_course = course_info[codes[0]]
                course_info[pseudo_code] = {
                    "course_code": pseudo_code,
                    "course_name": f"{prefix_label} (Parallel Group)",
                    "L": first_course.get("L", 0),
                    "T": first_course.get("T", 0),
                    "P": first_course.get("P", 0),
                    "semester": sem,
                    "lecture_in_lab": "No",
                    "tutorial_in_lab": "No",
                    "elective": "Yes"
                }
                for sec, courses in section_courses.items():
                    if sec.startswith(sem):
                        filtered = [c for c in courses if c not in codes]
                        if len(filtered) < len(courses):
                            filtered.append(pseudo_code)
                        section_courses[sec] = filtered
                for fac, assigns in faculty_assignments.items():
                    new_assigns = []
                    for (sec, cc) in assigns:
                        if cc in codes and sec.startswith(sem):
                            new_assigns.append((sec, pseudo_code))
                        else:
                            new_assigns.append((sec, cc))
                    faculty_assignments[fac] = list(dict.fromkeys(new_assigns))
            elif len(codes) == 1:
                new_codes.add(codes[0])

        for code in elective_codes:
            if code not in course_info:
                new_codes.add(code)

        return new_codes

    oe_codes  = group_parallel_electives(oe_codes,  "OE")
    aec_codes = group_parallel_electives(aec_codes, "AEC")

    pg_core_name = constraints_doc.get("pg_shared_core")
    pg_core_code = name_to_code.get(pg_core_name, pg_core_name) if pg_core_name else None

    pg_pe_codes = [code for code, info in course_info.items()
                   if str(info.get("elective", "No")).lower() in ("yes", "y", "true")
                   and str(info.get("ug_pg", "UG")).upper() == "PG"]

    lab_alloc = constraints_doc.get("cse_lab_allocations", [])

    sections_3rd = [s for s in section_courses if s.startswith("3")]
    sections_4th = [s for s in section_courses if s.startswith("4")]
    pg_sections  = [s for s in section_courses if "PG" in s or "SP" in s]

    return {
        "section_courses":    section_courses,
        "faculty_assignments": faculty_assignments,
        "faculty_designations": faculty_designations,
        "oe_codes":           oe_codes,
        "aec_codes":          aec_codes,
        "pg_core_code":       pg_core_code,
        "pg_pe_codes":        pg_pe_codes,
        "maths_slots":        maths_slots,
        "lab_alloc":          lab_alloc,
        "sections_3rd":       sections_3rd,
        "sections_4th":       sections_4th,
        "pg_sections":        pg_sections,
    }


# -----------------------------------------------------------------------
# Model building
# -----------------------------------------------------------------------
def _create_variables(model, section_courses, course_info, faculty_designations,
                      faculty_assignments):
    """Create x1 (lecture), x2 (tutorial/practical block), and co_fac (co-faculty) BoolVars."""
    x1 = {}
    x2 = {}
    co_fac = {}

    primary_faculty_for = {}
    for fac_name, assignments in faculty_assignments.items():
        for sec, cc in assignments:
            primary_faculty_for.setdefault((sec, cc), set()).add(fac_name)

    all_faculty = list(faculty_designations.keys())

    for sec, courses in section_courses.items():
        for cc in courses:
            info = course_info.get(cc, {})
            L = info.get("L", 0)
            T = info.get("T", 0)
            P = info.get("P", 0)

            if L > 0:
                for d in range(NUM_DAYS):
                    for t in range(NUM_SLOTS):
                        x1[(sec, cc, d, t)] = model.NewBoolVar(f"lec_{sec}_{cc}_d{d}_t{t}")

            if T > 0:
                for d in range(NUM_DAYS):
                    for t in VALID_BLOCK_STARTS:
                        x2[(sec, cc, "T", d, t)] = model.NewBoolVar(f"tut_{sec}_{cc}_d{d}_t{t}")

            if P > 0:
                primary_fac   = primary_faculty_for.get((sec, cc), set())
                eligible_cofac = [f for f in all_faculty if f not in primary_fac]
                for d in range(NUM_DAYS):
                    for t in VALID_BLOCK_STARTS:
                        x2[(sec, cc, "P", d, t)] = model.NewBoolVar(f"prac_{sec}_{cc}_d{d}_t{t}")
                        for fac_name in eligible_cofac:
                            k_cf = (fac_name, sec, cc, d, t)
                            co_fac[k_cf] = model.NewBoolVar(f"cofac_{fac_name}_{sec}_{cc}_{d}_{t}")

    return x1, x2, co_fac


# -----------------------------------------------------------------------
# Solution extraction
# -----------------------------------------------------------------------
def _extract_solution(solver, x1, x2, co_fac, lab_room, section_courses,
                      course_info, faculty_assignments, faculty_designations, semester):
    """Read solved variable values and build timetable grids + workload summary.

    Returns
    -------
    section_timetables : {section: [[cell, ...] * 7] * 5}
    faculty_timetables : {faculty: [[cell, ...] * 7] * 5}
    workload           : {faculty: {"scheduled": N, "cap": M, "designation": str, "pct": float}}
    """
    # Build reverse lookup: (sec, cc) -> faculty name(s)
    sec_cc_to_faculty: dict[tuple, str] = {}
    for fac_name, assignments in faculty_assignments.items():
        for sec, cc in assignments:
            existing = sec_cc_to_faculty.get((sec, cc))
            if existing:
                sec_cc_to_faculty[(sec, cc)] = f"{existing} / {fac_name}"
            else:
                sec_cc_to_faculty[(sec, cc)] = fac_name

    # --- Section timetables ---
    section_tt = {}
    for sec in section_courses:
        grid = [["" for _ in range(NUM_SLOTS)] for _ in range(NUM_DAYS)]
        for cc in section_courses[sec]:
            info       = course_info.get(cc, {})
            name       = info.get("course_name", cc)
            fac_label  = sec_cc_to_faculty.get((sec, cc), "")
            fac_suffix = f"\n{fac_label}" if fac_label else ""
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1 and solver.Value(x1[key]) == 1:
                        room_name = ""
                        for room in LAB_ROOMS:
                            rk = (sec, cc, "L", d, t, room)
                            if rk in lab_room and solver.Value(lab_room[rk]) == 1:
                                room_name = room
                                break
                        room_suffix = f"\n{room_name}" if room_name else ""
                        grid[d][t] = f"{cc}\n({name})\n[L]{fac_suffix}{room_suffix}"
            for d in range(NUM_DAYS):
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2 and solver.Value(x2[key]) == 1:
                            short = "T" if etype == "T" else "P"
                            room_name = ""
                            for room in LAB_ROOMS:
                                rk = (sec, cc, etype, d, t, room)
                                if rk in lab_room and solver.Value(lab_room[rk]) == 1:
                                    room_name = room
                                    break
                            room_suffix = f"\n{room_name}" if room_name else ""
                            grid[d][t]     = f"{cc}\n({name})\n[{short}]{fac_suffix}{room_suffix}"
                            grid[d][t + 1] = f"{cc}\n({name})\n[{short}]{fac_suffix}{room_suffix}"
        section_tt[sec] = grid

    # --- Faculty timetables ---
    faculty_tt = {}
    for fac, assignments in faculty_assignments.items():
        grid = [["" for _ in range(NUM_SLOTS)] for _ in range(NUM_DAYS)]
        for sec, cc in assignments:
            info = course_info.get(cc, {})
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1 and solver.Value(x1[key]) == 1:
                        grid[d][t] = f"{cc} ({sec}) [L]"
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2 and solver.Value(x2[key]) == 1:
                            short = "T" if etype == "T" else "P"
                            grid[d][t]     = f"{cc} ({sec}) [{short}]"
                            grid[d][t + 1] = f"{cc} ({sec}) [{short}]"

        for (fac_name, sec, cc, d, t), var in co_fac.items():
            if fac_name == fac and solver.Value(var) == 1:
                grid[d][t]     = f"{cc} ({sec}) [Co-Fac P]"
                grid[d][t + 1] = f"{cc} ({sec}) [Co-Fac P]"

        faculty_tt[fac] = grid

    # --- Workload summary (#5) ---
    if semester.lower() == "odd":
        caps_units = {"Professor": 18, "Associate": 24, "Assistant": 28}
    else:
        caps_units = {"Professor": 14, "Associate": 18, "Assistant": 24}

    workload = {}
    for fac, assignments in faculty_assignments.items():
        scheduled = 0
        for sec, cc in assignments:
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    if (sec, cc, d, t) in x1 and solver.Value(x1[(sec, cc, d, t)]) == 1:
                        scheduled += 1
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        if (sec, cc, etype, d, t) in x2 and solver.Value(x2[(sec, cc, etype, d, t)]) == 1:
                            scheduled += 1

        for (fn, sec, cc, d, t), var in co_fac.items():
            if fn == fac and solver.Value(var) == 1:
                scheduled += 1

        desig = faculty_designations.get(fac, "Assistant")
        # Normalise designation key
        desig_key = desig.split()[0] if desig else "Assistant"
        cap_units  = caps_units.get(desig_key, 28)
        cap        = cap_units // 2
        pct        = round(100 * scheduled / cap, 1) if cap > 0 else 0.0
        workload[fac] = {
            "scheduled":   scheduled,
            "cap":         cap,
            "designation": desig,
            "pct":         pct,
        }

    return section_tt, faculty_tt, workload


# -----------------------------------------------------------------------
# Infeasibility diagnosis (#11)
# -----------------------------------------------------------------------
def _generate_infeasibility_hints(semester: str, section_map: dict | None, hint_time: int = 15) -> list[str]:
    """Run up to 3 diagnostic solves to identify the infeasibility cause.

    Each pass relaxes constraints progressively. The first pass that
    becomes feasible identifies which constraint group is responsible.

    Parameters
    ----------
    hint_time : time limit (seconds) per diagnostic pass
    """
    hints = []

    _ALL_OPTIONAL = {
        "no_student_gaps", "morning_first", "no_empty_days",
        "friday_half_day", "spread", "first_slot",
        "oe", "aec", "pg_shared", "maths", "cse_labs", "lab_rooms",
        "workload",
    }

    # Pass 1: Bare minimum — only H1, H2, H3 (no workload, no quality, no special)
    r1 = build_and_solve(
        semester=semester, time_limit_seconds=hint_time,
        section_map=section_map, _skip_constraints=_ALL_OPTIONAL
    )
    if r1["status"] in ("INFEASIBLE", "UNKNOWN", "MODEL_INVALID"):
        hints.append(
            "**Root cause:** Even the bare-minimum constraints (faculty clash, "
            "section clash, weekly hours) are unsatisfiable."
        )
        hints.append(
            "This usually means: a section has more course events than the 35 "
            "available weekly slots, or a faculty member's assignment list is inconsistent."
        )
        hints.append("Run the **Pre-flight Check** above for specific section/hour details.")
        return hints

    # Pass 2: Add workload caps (H16)
    r2 = build_and_solve(
        semester=semester, time_limit_seconds=hint_time,
        section_map=section_map,
        _skip_constraints=_ALL_OPTIONAL - {"workload"},
    )
    if r2["status"] in ("INFEASIBLE", "UNKNOWN", "MODEL_INVALID"):
        hints.append(
            "**Root cause:** Adding **workload caps (H16)** makes the model infeasible."
        )
        hints.append(
            "Faculty members are assigned to more courses/sections than their "
            "designation allows. Check the Workload Summary after a feasible solve, "
            "or reduce assignments in the Input Data."
        )
        return hints

    # Pass 3: Add special subject constraints (OE/AEC/Maths/Labs) but no quality constraints
    _QUALITY = {"no_student_gaps", "morning_first", "no_empty_days", "spread", "first_slot"}
    r3 = build_and_solve(
        semester=semester, time_limit_seconds=hint_time,
        section_map=section_map, _skip_constraints=_QUALITY,
    )
    if r3["status"] in ("INFEASIBLE", "UNKNOWN", "MODEL_INVALID"):
        hints.append(
            "**Root cause:** The **special subject constraints** (OE concurrency, "
            "AEC concurrency, PG shared classes, Maths locks, or CSE lab blocks) "
            "create a conflict."
        )
        hints.append(
            "Check your **Constraints page** — look for maths locks that overlap "
            "with OE/AEC slots, or lab allocations that block required practical times."
        )
        return hints

    # If we got here, only the quality constraints cause infeasibility
    hints.append(
        "**Root cause:** The **scheduling quality constraints** (no student gaps, "
        "morning-first, subject spread) make the full model infeasible."
    )
    hints.append(
        "The data *is* solvable without these constraints. "
        "Try increasing the solver time limit significantly (5–10 min), "
        "or check if the Friday half-day constraint is too restrictive."
    )
    return hints


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------
def build_and_solve(
    semester: str = "odd",
    time_limit_seconds: int = 60,
    num_workers: int = 8,
    section_map: dict | None = None,
    progress_queue: "_queue_mod.Queue | None" = None,
    _skip_constraints: "set | None" = None,
) -> dict:
    """Main entry point. Loads data, builds CP-SAT model, solves, returns results.

    Parameters
    ----------
    semester            : "odd" or "even"
    time_limit_seconds  : solver time limit
    num_workers         : parallel CP-SAT workers (default 8)
    section_map         : optional semester->sections override (from Settings page)
    progress_queue      : if provided, push solution-found log messages here
    _skip_constraints   : set of constraint group names to omit (used by IIS diagnosis)

    Returns
    -------
    dict with keys:
        status              : "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN" | "MODEL_INVALID"
        timetables          : {section: 5x7 grid}
        faculty_timetables  : {faculty: 5x7 grid}
        workload            : {faculty: {scheduled, cap, designation, pct}}
        stats               : {solve_time, branches, conflicts, variables, constraints}
        errors              : list of error strings
        infeasibility_hints : list of diagnostic strings (only when INFEASIBLE)
    """
    skip = _skip_constraints or set()
    is_diagnostic = bool(_skip_constraints)  # Don't recurse into IIS from diagnostic calls

    result = {
        "status":               "UNKNOWN",
        "timetables":           {},
        "faculty_timetables":   {},
        "workload":             {},
        "stats":                {},
        "errors":               [],
        "infeasibility_hints":  [],
    }

    # --- Load data ---
    try:
        data = _load_data(semester)
    except Exception as e:
        result["errors"].append(f"Data loading failed: {e}")
        return result

    course_info = data["course_info"]
    mappings    = _build_mappings(course_info, data["faculty_raw"],
                                  data["constraints_doc"], section_map=section_map)

    section_courses    = mappings["section_courses"]
    faculty_assignments = mappings["faculty_assignments"]

    if not section_courses:
        result["errors"].append("No sections/courses found. Upload data first.")
        return result

    # --- Build model ---
    model = cp_model.CpModel()
    x1, x2, co_fac = _create_variables(model, section_courses, course_info,
                                        mappings["faculty_designations"],
                                        mappings["faculty_assignments"])

    # --- Precomputed index maps ---
    slot_coverage_sec  = defaultdict(list)
    event_vars_sec     = defaultdict(list)
    course_day_events  = defaultdict(list)
    x1_by_sec_cc       = defaultdict(list)
    x1_keys_by_sec_cc  = defaultdict(list)
    x1_t0_by_sec_cc    = defaultdict(list)

    for (sec, cc, d, t), var in x1.items():
        slot_coverage_sec[(sec, d, t)].append(var)
        event_vars_sec[(sec, d)].append(var)
        course_day_events[(sec, cc, d)].append(var)
        x1_by_sec_cc[(sec, cc)].append(var)
        x1_keys_by_sec_cc[(sec, cc)].append((d, t, var))
        if t == 0:
            x1_t0_by_sec_cc[(sec, cc)].append(var)

    x2T_by_sec_cc      = defaultdict(list)
    x2P_by_sec_cc      = defaultdict(list)
    x2_by_sec_cc       = defaultdict(list)
    x2_by_sec_dt_etype = defaultdict(list)
    x2_t0_by_sec_cc    = defaultdict(list)

    for (sec, cc, etype, d, t_start), var in x2.items():
        slot_coverage_sec[(sec, d, t_start)].append(var)
        slot_coverage_sec[(sec, d, t_start + 1)].append(var)
        event_vars_sec[(sec, d)].append(var)
        course_day_events[(sec, cc, d)].append(var)
        x2_by_sec_cc[(sec, cc)].append(var)
        x2_by_sec_dt_etype[(sec, d, t_start, etype)].append(var)
        if etype == "T":
            x2T_by_sec_cc[(sec, cc)].append(var)
        else:
            x2P_by_sec_cc[(sec, cc)].append(var)
        if t_start == 0:
            x2_t0_by_sec_cc[(sec, cc)].append(var)

    events_by_fac = defaultdict(list)
    for fac, assignments in mappings["faculty_assignments"].items():
        for sec, cc in assignments:
            events_by_fac[fac].extend(x1_by_sec_cc.get((sec, cc), []))
            events_by_fac[fac].extend(x2_by_sec_cc.get((sec, cc), []))

    # ---- Apply hard constraints (with skip support) ----
    add_no_faculty_clash(model, x1, x2, co_fac, mappings["faculty_assignments"],
                         mappings["pg_core_code"], mappings["pg_sections"])

    if "co_faculty_logic" not in skip:
        add_co_faculty_logic(model, x2, co_fac, mappings["faculty_assignments"])

    if "workload" not in skip:
        add_max_workload(model, co_fac, mappings["faculty_assignments"],
                         mappings["faculty_designations"], events_by_fac, semester)

    add_no_section_clash(model, x1, x2, section_courses)
    add_weekly_hours(model, section_courses, course_info,
                     x1_by_sec_cc, x2T_by_sec_cc, x2P_by_sec_cc)

    if "no_student_gaps" not in skip:
        add_no_student_gaps(model, section_courses, slot_coverage_sec)

    # CSE lab locks — returns blocked (room, day, slot) tuples
    if "cse_labs" not in skip:
        _blocked = add_cse_lab_locks(model, x1, x2, mappings["lab_alloc"])
    else:
        _blocked = []

    if "lab_rooms" not in skip:
        lab_room = add_lab_room_assignment(model, x1, x2, section_courses, course_info,
                                           mappings["pg_sections"], _blocked)
    else:
        lab_room = {}

    if "friday_half_day" not in skip:
        add_friday_half_day(model, x1, x2, section_courses, course_day_events)

    if "morning_first" not in skip:
        add_morning_first(model, section_courses, slot_coverage_sec)

    if "no_empty_days" not in skip:
        add_no_empty_days(model, section_courses, event_vars_sec)

    if "spread" not in skip:
        add_spread_constraint(model, section_courses, course_day_events)

    if "first_slot" not in skip:
        add_first_slot_constraint(model, section_courses, x1_t0_by_sec_cc, x2_t0_by_sec_cc)

    if "oe" not in skip and mappings["oe_codes"]:
        add_oe_concurrency(model, section_courses, mappings["oe_codes"], x1_keys_by_sec_cc)

    if "aec" not in skip and mappings["aec_codes"]:
        add_aec_concurrency(model, section_courses, mappings["aec_codes"],
                            mappings["sections_3rd"], mappings["sections_4th"],
                            x1_keys_by_sec_cc)

    if "pg_shared" not in skip and mappings["pg_sections"]:
        add_pg_shared(model, section_courses, mappings["pg_sections"],
                      mappings["pg_core_code"], mappings["pg_pe_codes"],
                      x1_by_sec_cc, x2_by_sec_dt_etype)

    if "maths" not in skip and mappings["maths_slots"]:
        add_maths_locks(model, x1, x2, mappings["maths_slots"])

    # ---- Soft objective ----
    penalties = []
    fac_taught_courses = {}
    for fac, assigns in mappings["faculty_assignments"].items():
        fac_taught_courses[fac] = {cc for (_, cc) in assigns}

    fac_mismatch_vars = defaultdict(list)
    all_mismatch_vars = []
    for (fac_name, sec, cc, d, t), var in co_fac.items():
        taught = fac_taught_courses.get(fac_name, set())
        if cc not in taught:
            fac_mismatch_vars[fac_name].append(var)
            all_mismatch_vars.append(var)

    for fac_name, vars_list in fac_mismatch_vars.items():
        if vars_list:
            penalties.append(100 * sum(vars_list))

    if penalties:
        model.Minimize(sum(penalties))

    # ---- Search strategy ----
    all_x2 = list(x2.values())
    if all_x2:
        model.AddDecisionStrategy(all_x2, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE)
    if all_mismatch_vars:
        model.AddDecisionStrategy(all_mismatch_vars, cp_model.CHOOSE_FIRST, cp_model.SELECT_MIN_VALUE)

    # ---- Solve ----
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds  = time_limit_seconds
    solver.parameters.num_workers          = max(1, int(num_workers))
    solver.parameters.log_search_progress  = False  # We use the callback instead
    solver.parameters.relative_gap_limit   = 0.05
    solver.parameters.absolute_gap_limit   = 30.0
    solver.parameters.linearization_level  = 1
    solver.parameters.interleave_search    = True

    if progress_queue is not None and not is_diagnostic:
        callback    = _TimetableProgressCallback(progress_queue)
        status_code = solver.Solve(model, callback)  # callback arg supported in OR-Tools 9.4+
    else:
        status_code = solver.Solve(model)

    status_map = {
        cp_model.OPTIMAL:       "OPTIMAL",
        cp_model.FEASIBLE:      "FEASIBLE",
        cp_model.INFEASIBLE:    "INFEASIBLE",
        cp_model.UNKNOWN:       "UNKNOWN",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
    }
    result["status"] = status_map.get(status_code, "UNKNOWN")
    result["stats"]  = {
        "solve_time_s":  round(solver.WallTime(), 2),
        "branches":      solver.NumBranches(),
        "conflicts":     solver.NumConflicts(),
        "num_variables": len(x1) + len(x2),
        "num_sections":  len(section_courses),
        "num_courses":   len(course_info),
    }

    if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["timetables"], result["faculty_timetables"], result["workload"] = (
            _extract_solution(solver, x1, x2, co_fac, lab_room, section_courses,
                              course_info, faculty_assignments,
                              mappings["faculty_designations"], semester)
        )

    elif status_code == cp_model.INFEASIBLE:
        result["errors"].append(
            "Model is INFEASIBLE -- the constraints are contradictory. "
            "Check if the weekly hours fit in the available slots and "
            "there are enough faculty members."
        )
        # Only run IIS diagnosis for the primary (non-diagnostic) solve
        if not is_diagnostic:
            result["infeasibility_hints"] = _generate_infeasibility_hints(
                semester, section_map
            )

    return result
