"""
solver.py — OR-Tools CP-SAT timetable solver.

Public API:
    build_and_solve(semester, time_limit_seconds) -> dict

Flow:
    1. Load courses, faculty, constraints from MongoDB
    2. Derive section→course and faculty→(section,course) mappings
    3. Create BoolVars for lectures (x1) and 2-slot blocks (x2)
    4. Apply all hard + soft constraints
    5. Solve
    6. Extract & return timetable grids
"""

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
    add_faculty_break,
    add_co_faculty_break,      # TEMPORARY FIX — H5.5
    add_oe_concurrency,
    add_aec_concurrency,
    add_pg_shared,
    add_maths_locks,
    add_cse_lab_locks,
    add_spread_penalty,
    add_first_slot_repeat_penalty,
    add_co_faculty_logic,
    add_max_workload,
    add_lab_room_assignment,
    add_friday_half_day,
    add_faculty_morning_penalty,
    add_morning_first,
    add_no_empty_days,
)

# ---------------------------------------------------------------------------
# TEMPORARY FIX FLAGS — set to False to revert individual fixes
# ---------------------------------------------------------------------------
# Fix A: Exclude co-faculty slots from the primary workload cap.
#        Prevents co-faculty duty from crowding out a faculty's own lectures.
EXCLUDE_COFAC_FROM_WORKLOAD_CAP: bool = True

# Fix B: Enforce a 1-slot break between primary teaching and co-faculty blocks.
#        Prevents faculty being co-faculty immediately before/after their lectures.
ENABLE_CO_FACULTY_BREAK: bool = True
# ---------------------------------------------------------------------------

DAYS_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SLOTS_LABELS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]

# -----------------------------------------------------------------------
# Helper: semester string → sections
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


def _sections_for_semester(sem_str: str) -> list[str]:
    """Map a semester string (from course/faculty data) to section list."""
    s = str(sem_str).strip()
    
    # 1. Exact match for a whole semester (e.g. "3" -> ["3A", "3B", "3C", "3D"])
    if s in _SEMESTER_SECTIONS:
        return _SEMESTER_SECTIONS[s]
        
    for key in _SEMESTER_SECTIONS:
        if s.lower() == key.lower():
            return _SEMESTER_SECTIONS[key]
            
    # 2. Check if it's a comma-separated list of specific sections (e.g. "3A", "3A, 3B")
    all_sections = set()
    for secs in _SEMESTER_SECTIONS.values():
        all_sections.update(secs)
        
    parsed_sections = []
    # Replace slashes with commas to support "3A/3B" formats too
    parts = [p.strip() for p in s.replace('/', ',').split(',')]
    for part in parts:
        part_upper = part.upper()
        # Find matching section ignoring case
        match = next((sec for sec in all_sections if sec.upper() == part_upper), None)
        if match:
            if match not in parsed_sections:
                parsed_sections.append(match)
            
    if parsed_sections:
        return parsed_sections
        
    return []


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


def _build_mappings(course_info, faculty_raw, constraints_doc):
    """
    Derive:
        section_courses   : {section -> [course_codes]}
        faculty_assignments: {faculty_name -> [(section, course_code)]}
        oe_codes, aec_codes, pg shared info, maths_slots, lab_alloc
    """
    # --- Which semesters are we scheduling? ---
    # Collect all semesters present in courses
    semesters_in_data = set()
    for code, info in course_info.items():
        sem = info.get("semester", "")
        if sem:
            semesters_in_data.add(sem)

    # --- Extract PG common codes first to use in section assignment ---
    pg_core_code = constraints_doc.get("pg_shared_core", "None")
    if pg_core_code == "None": pg_core_code = None
    pg_pe_codes = constraints_doc.get("pg_shared_pe", [])
    
    # --- section → course list ---
    section_courses: dict[str, list[str]] = {}
    for code, info in course_info.items():
        sem = info.get("semester", "")
        ug_pg = str(info.get("ug_pg", "UG")).strip().upper()
        
        if ug_pg == "PG":
            # Only schedule PG 1st and 2nd sem; skip 3rd and 4th sem
            if sem not in ("1", "2"):
                continue

            _ordinal = {"1": "1st", "2": "2nd"}
            label = f"PG {_ordinal[sem]} Sem"
            sections = []
            is_common = (code in pg_pe_codes) or (code == pg_core_code)
            # Route MCS to SP1, MCN to SP2. Shared subjects go to both.
            if "MCS" in code or is_common:
                sections.append(f"{label} - SP1")
            if "MCN" in code or is_common:
                sections.append(f"{label} - SP2")
        else:
            sections = _sections_for_semester(sem)
            
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
    # Add MATHS to course_info with enough L hours
    if maths_sections:
        # Count how many maths slots per section (assume all sections same)
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

    # --- faculty → (section, course) assignments ---
    # Strategy: group faculty by (course_code, semester). Assign round-robin
    # to available sections for that semester.
    faculty_by_course: dict[str, list[str]] = {}  # (code, sem) -> [faculty names]
    faculty_all_courses: dict[str, list[dict]] = {}  # fac_name -> [{code, sem}]
    faculty_designations: dict[str, str] = {} # fac_name -> designation
    for fac in faculty_raw:
        name = fac.get("name", "")
        faculty_designations[name] = str(fac.get("designation", "Assistant")).strip()
        for subj in fac.get("subjects", []):
            code = subj.get("code", "")
            sem = subj.get("semester", "")
            key = f"{code}|{sem}"
            faculty_by_course.setdefault(key, []).append(name)
            faculty_all_courses.setdefault(name, []).append(
                {"code": code, "semester": sem, "type": "subject"}
            )
        for lab in fac.get("labs", []):
            code = lab.get("code", "")
            sem = lab.get("semester", "")
            key = f"{code}|{sem}"
            faculty_by_course.setdefault(key, []).append(name)
            faculty_all_courses.setdefault(name, []).append(
                {"code": code, "semester": sem, "type": "lab"}
            )

    faculty_assignments: dict[str, list[tuple[str, str]]] = {}
    assigned_sections: dict[str, set[str]] = {}  # (code, sem) -> set of assigned sections

    for key_str, fac_list in faculty_by_course.items():
        code, sem = key_str.split("|", 1)
        sections = _sections_for_semester(sem)
        if not sections:
            continue
        assigned_sections.setdefault(key_str, set())
        for i, fac_name in enumerate(fac_list):
            if i < len(sections):
                sec = sections[i]
                faculty_assignments.setdefault(fac_name, []).append((sec, code))
                assigned_sections[key_str].add(sec)

        # If fewer faculty than sections, distribute remaining sections
        # among existing faculty (one faculty teaches multiple sections)
        remaining = [s for s in sections if s not in assigned_sections[key_str]]
        if remaining and fac_list:
            for j, sec in enumerate(remaining):
                fac_name = fac_list[j % len(fac_list)]
                faculty_assignments.setdefault(fac_name, []).append((sec, code))

    # --- OE / AEC / PG codes ---
    # The constraints store course NAMES. We need to map back to codes.
    name_to_code = {}
    for code, info in course_info.items():
        name_to_code[info.get("course_name", "")] = code

    oe_names = constraints_doc.get("open_electives", [])
    oe_codes = set(name_to_code.get(n, n) for n in oe_names)

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
                
                # Take the first course's L, T, P for the pseudo course
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
                
                # Replace in section_courses
                for sec, courses in section_courses.items():
                    if sec.startswith(sem):
                        filtered = [c for c in courses if c not in codes]
                        if len(filtered) < len(courses):
                            filtered.append(pseudo_code)
                        section_courses[sec] = filtered
                        
                # Replace in faculty_assignments
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
                
        # Also include any codes that were not found in course_info (keep as is)
        for code in elective_codes:
            if code not in course_info:
                new_codes.add(code)
                
        return new_codes

    oe_codes = group_parallel_electives(oe_codes, "OE")
    aec_codes = group_parallel_electives(aec_codes, "AEC")

    pg_core_name = constraints_doc.get("pg_shared_core")
    pg_core_code = name_to_code.get(pg_core_name, pg_core_name) if pg_core_name else None

    # Auto-calculate PG Professional Elective codes from course metadata
    pg_pe_codes = [code for code, info in course_info.items() 
                   if str(info.get("elective", "No")).lower() in ("yes", "y", "true") 
                   and str(info.get("ug_pg", "UG")).upper() == "PG"]

    lab_alloc = constraints_doc.get("cse_lab_allocations", [])

    # Identify section groups
    sections_3rd = [s for s in section_courses if s.startswith("3")]
    sections_4th = [s for s in section_courses if s.startswith("4")]
    pg_sections = [s for s in section_courses if "PG" in s or "SP" in s]

    return {
        "section_courses": section_courses,
        "faculty_assignments": faculty_assignments,
        "faculty_designations": faculty_designations,
        "oe_codes": oe_codes,
        "aec_codes": aec_codes,
        "pg_core_code": pg_core_code,
        "pg_pe_codes": pg_pe_codes,
        "maths_slots": maths_slots,
        "lab_alloc": lab_alloc,
        "sections_3rd": sections_3rd,
        "sections_4th": sections_4th,
        "pg_sections": pg_sections,
    }


# -----------------------------------------------------------------------
# Model building
# -----------------------------------------------------------------------
def _create_variables(model, section_courses, course_info, faculty_designations):
    """Create x1 (lecture), x2 (tutorial/practical block), and co_fac (co-faculty) BoolVars."""
    x1 = {}
    x2 = {}
    co_fac = {} # (fac_name, sec, cc, d, t) -> BoolVar

    for sec, courses in section_courses.items():
        for cc in courses:
            info = course_info.get(cc, {})
            L = info.get("L", 0)
            T = info.get("T", 0)
            P = info.get("P", 0)

            # Lecture vars (only if L > 0)
            if L > 0:
                for d in range(NUM_DAYS):
                    for t in range(NUM_SLOTS):
                        x1[(sec, cc, d, t)] = model.NewBoolVar(
                            f"lec_{sec}_{cc}_d{d}_t{t}"
                        )

            # Tutorial block vars (only if T > 0)
            if T > 0:
                for d in range(NUM_DAYS):
                    for t in VALID_BLOCK_STARTS:
                        x2[(sec, cc, "T", d, t)] = model.NewBoolVar(
                            f"tut_{sec}_{cc}_d{d}_t{t}"
                        )

            # Practical block vars (only if P > 0)
            if P > 0:
                for d in range(NUM_DAYS):
                    for t in VALID_BLOCK_STARTS:
                        x2[(sec, cc, "P", d, t)] = model.NewBoolVar(
                            f"prac_{sec}_{cc}_d{d}_t{t}"
                        )
                        # Create dynamic co-faculty assignment variables for every faculty member for this specific practical block
                        for fac_name in faculty_designations.keys():
                            k_cf = (fac_name, sec, cc, d, t)
                            co_fac[k_cf] = model.NewBoolVar(f"cofac_{fac_name}_{sec}_{cc}_{d}_{t}")

    return x1, x2, co_fac


# -----------------------------------------------------------------------
# Solution extraction
# -----------------------------------------------------------------------
def _extract_solution(solver, x1, x2, co_fac, lab_room, section_courses,
                      course_info, faculty_assignments):
    """
    Read solved variable values and build timetable grids.

    Returns:
        section_timetables: {section: [[cell, ...] * 7] * 5}
        faculty_timetables: {faculty: [[cell, ...] * 7] * 5}
    """
    # Section timetables
    # Build reverse lookup: (sec, cc) -> faculty name(s) for display in class timetables
    sec_cc_to_faculty: dict[tuple, str] = {}
    for fac_name, assignments in faculty_assignments.items():
        for sec, cc in assignments:
            existing = sec_cc_to_faculty.get((sec, cc))
            if existing:
                sec_cc_to_faculty[(sec, cc)] = f"{existing} / {fac_name}"
            else:
                sec_cc_to_faculty[(sec, cc)] = fac_name

    section_tt = {}
    for sec in section_courses:
        grid = [["" for _ in range(NUM_SLOTS)] for _ in range(NUM_DAYS)]
        for cc in section_courses[sec]:
            info = course_info.get(cc, {})
            name = info.get("course_name", cc)
            fac_label = sec_cc_to_faculty.get((sec, cc), "")
            fac_suffix = f"\n{fac_label}" if fac_label else ""
            # Lectures
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1 and solver.Value(x1[key]) == 1:
                        # Find assigned room (if any, like for AEC)
                        room_name = ""
                        for room in LAB_ROOMS:
                            rk = (sec, cc, "L", d, t, room)
                            if rk in lab_room and solver.Value(lab_room[rk]) == 1:
                                room_name = room
                                break
                        room_suffix = f"\n{room_name}" if room_name else ""
                        grid[d][t] = f"{cc}\n({name})\n[L]{fac_suffix}{room_suffix}"
            # Blocks
            for d in range(NUM_DAYS):
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2 and solver.Value(x2[key]) == 1:
                            short = "T" if etype == "T" else "P"
                            # Find assigned room
                            room_name = ""
                            for room in LAB_ROOMS:
                                rk = (sec, cc, etype, d, t, room)
                                if rk in lab_room and solver.Value(lab_room[rk]) == 1:
                                    room_name = room
                                    break
                            room_suffix = f"\n{room_name}" if room_name else ""
                            grid[d][t] = f"{cc}\n({name})\n[{short}]{fac_suffix}{room_suffix}"
                            grid[d][t + 1] = f"{cc}\n({name})\n[{short}]{fac_suffix}{room_suffix}"
        section_tt[sec] = grid

    # Faculty timetables
    faculty_tt = {}
    for fac, assignments in faculty_assignments.items():
        grid = [["" for _ in range(NUM_SLOTS)] for _ in range(NUM_DAYS)]
        for sec, cc in assignments:
            info = course_info.get(cc, {})
            name = info.get("course_name", cc)
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
                            grid[d][t] = f"{cc} ({sec}) [{short}]"
                            grid[d][t + 1] = f"{cc} ({sec}) [{short}]"
        
        # Add their co-faculty assignments!
        for (fac_name, sec, cc, d, t), var in co_fac.items():
            if fac_name == fac and solver.Value(var) == 1:
                grid[d][t] = f"{cc} ({sec}) [Co-Fac P]"
                grid[d][t + 1] = f"{cc} ({sec}) [Co-Fac P]"
                
        faculty_tt[fac] = grid

    return section_tt, faculty_tt


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------
def build_and_solve(semester: str = "odd", time_limit_seconds: int = 60):
    """
    Main entry point. Loads data, builds CP-SAT model, solves, returns results.

    Parameters:
        semester: "odd" or "even"
        time_limit_seconds: solver time limit

    Returns dict:
        status: "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN" | "MODEL_INVALID"
        timetables: {section: 5×7 grid}
        faculty_timetables: {faculty: 5×7 grid}
        stats: {solve_time, branches, conflicts, variables, constraints}
        errors: list of error strings (empty on success)
    """
    result = {
        "status": "UNKNOWN",
        "timetables": {},
        "faculty_timetables": {},
        "stats": {},
        "errors": [],
    }

    # --- Load data ---
    try:
        data = _load_data(semester)
    except Exception as e:
        result["errors"].append(f"Data loading failed: {e}")
        return result

    course_info = data["course_info"]
    mappings = _build_mappings(course_info, data["faculty_raw"],
                               data["constraints_doc"])

    section_courses = mappings["section_courses"]
    faculty_assignments = mappings["faculty_assignments"]

    if not section_courses:
        result["errors"].append("No sections/courses found. Upload data first.")
        return result

    # --- Build model ---
    model = cp_model.CpModel()
    x1, x2, co_fac = _create_variables(model, section_courses, course_info, mappings["faculty_designations"])

    # Hard constraints
    add_no_faculty_clash(model, x1, x2, co_fac, mappings["faculty_assignments"], mappings["pg_core_code"], mappings["pg_sections"])
    add_co_faculty_logic(model, x2, co_fac, mappings["faculty_assignments"])
    add_max_workload(model, x1, x2, co_fac, mappings["faculty_assignments"], mappings["faculty_designations"],
                     semester, count_cofac_in_workload=not EXCLUDE_COFAC_FROM_WORKLOAD_CAP)
    add_no_section_clash(model, x1, x2, section_courses)
    add_weekly_hours(model, x1, x2, section_courses, course_info)
    add_no_student_gaps(model, x1, x2, section_courses)
    add_faculty_break(model, x1, x2, faculty_assignments, co_fac=co_fac)
    if ENABLE_CO_FACULTY_BREAK:  # TEMPORARY FIX
        add_co_faculty_break(model, x1, x2, co_fac, faculty_assignments)

    # CSE lab locks — returns blocked (room, day, slot) tuples
    _blocked = add_cse_lab_locks(model, x1, x2, mappings["lab_alloc"])

    lab_room = add_lab_room_assignment(model, x1, x2, section_courses, course_info,
                                       mappings["pg_sections"], _blocked)
    add_friday_half_day(model, x1, x2, section_courses)
    add_morning_first(model, x1, x2, section_courses)
    add_no_empty_days(model, x1, x2, section_courses)

    # Special subject constraints
    if mappings["oe_codes"]:
        add_oe_concurrency(model, x1, section_courses, mappings["oe_codes"])
    if mappings["aec_codes"]:
        add_aec_concurrency(model, x1, section_courses, mappings["aec_codes"],
                            mappings["sections_3rd"], mappings["sections_4th"])
    if mappings["pg_sections"]:
        add_pg_shared(model, x1, x2, section_courses, mappings["pg_sections"],
                      mappings["pg_core_code"], mappings["pg_pe_codes"])
    if mappings["maths_slots"]:
        add_maths_locks(model, x1, x2, mappings["maths_slots"])


    # Soft constraints (objective)
    penalties = add_spread_penalty(model, x1, x2, section_courses)
    penalties.extend(add_faculty_morning_penalty(model, x1, x2, mappings["faculty_assignments"]))
    penalties.extend(add_first_slot_repeat_penalty(model, x1, x2, section_courses))
    
    # Soft Penalty: Co-faculty mismatch (penalize if fac_name doesn't normally teach this course code)
    # Build a set of course codes each faculty teaches
    fac_taught_courses = {}
    for fac, assigns in mappings["faculty_assignments"].items():
        fac_taught_courses[fac] = {cc for (_, cc) in assigns}
        
    # Aggregate mismatch penalties by faculty to simplify the objective landscape
    from collections import defaultdict
    fac_mismatch_vars = defaultdict(list)
    all_mismatch_vars = []
    for (fac_name, sec, cc, d, t), var in co_fac.items():
        taught = fac_taught_courses.get(fac_name, set())
        if cc not in taught:
            fac_mismatch_vars[fac_name].append(var)
            all_mismatch_vars.append(var)
            
    for fac_name, vars_list in fac_mismatch_vars.items():
        if vars_list:
            penalties.append(10 * sum(vars_list))
            
    if penalties:
        model.Minimize(sum(penalties))

    # --- Search Strategy ---
    # Prioritize scheduling Labs and Tutorials (x2) as they are the most restrictive.
    # We want to try assigning them first (SELECT_MAX_VALUE).
    all_x2 = list(x2.values())
    if all_x2:
        model.AddDecisionStrategy(all_x2, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE)
        
    # For penalty variables (like mismatches), we MUST use SELECT_MIN_VALUE so the solver
    # attempts to find 0-penalty solutions first rather than forcing maximum penalties.
    if all_mismatch_vars:
        model.AddDecisionStrategy(all_mismatch_vars, cp_model.CHOOSE_FIRST, cp_model.SELECT_MIN_VALUE)

    # --- Solve ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 8  # use multiple cores
    solver.parameters.log_search_progress = True
    solver.parameters.relative_gap_limit = 0.05  # stop if within 5% of optimal
    solver.parameters.absolute_gap_limit = 15.0  # stop if within 15 points of optimal
    solver.parameters.linearization_level = 2    # stronger LP relaxation bounds

    status_code = solver.Solve(model)

    status_map = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.UNKNOWN: "UNKNOWN",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
    }
    result["status"] = status_map.get(status_code, "UNKNOWN")
    result["stats"] = {
        "solve_time_s": round(solver.WallTime(), 2),
        "branches": solver.NumBranches(),
        "conflicts": solver.NumConflicts(),
        "num_variables": len(x1) + len(x2),
        "num_sections": len(section_courses),
        "num_courses": len(course_info),
    }

    if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["timetables"], result["faculty_timetables"] = _extract_solution(
            solver, x1, x2, co_fac, lab_room, section_courses, course_info,
            mappings["faculty_assignments"]
        )
    elif status_code == cp_model.INFEASIBLE:
        result["errors"].append(
            "Model is INFEASIBLE — the constraints are contradictory. "
            "Check if the weekly hours fit in the available slots and "
            "there are enough faculty members."
        )

    return result
