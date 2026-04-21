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
    add_no_faculty_clash,
    add_no_section_clash,
    add_weekly_hours,
    add_morning_filled,
    add_faculty_break,
    add_oe_concurrency,
    add_aec_concurrency,
    add_pg_shared,
    add_maths_locks,
    add_cse_lab_locks,
    add_spread_penalty,
)

DAYS_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SLOTS_LABELS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]

# -----------------------------------------------------------------------
# Helper: semester string → sections
# -----------------------------------------------------------------------
_SEMESTER_SECTIONS = {
    "3": ["3A", "3B", "3C", "3D"],
    "4": ["4A", "4B", "4C", "4D"],
    # PG — semester labels may differ; adjust if needed
    "PG1": ["SP1"],
    "PG2": ["SP2"],
}


def _sections_for_semester(sem_str: str) -> list[str]:
    """Map a semester string (from course/faculty data) to section list."""
    s = str(sem_str).strip()
    if s in _SEMESTER_SECTIONS:
        return _SEMESTER_SECTIONS[s]
    # Try common variants
    for key in _SEMESTER_SECTIONS:
        if s.lower() == key.lower():
            return _SEMESTER_SECTIONS[key]
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

    # --- section → course list ---
    section_courses: dict[str, list[str]] = {}
    for code, info in course_info.items():
        sem = info.get("semester", "")
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
        maths_per_section: dict[str, int] = {}
        for entry in maths_slots:
            cls = entry.get("Class", "")
            if cls:
                maths_per_section[cls] = maths_per_section.get(cls, 0) + 1
        max_maths = max(maths_per_section.values()) if maths_per_section else 0
        course_info["MATHS"] = {
            "L": max_maths, "T": 0, "P": 0,
            "semester": "", "course_name": "Mathematics",
            "lecture_in_lab": "No", "tutorial_in_lab": "No",
            "elective": "No",
        }

    # --- faculty → (section, course) assignments ---
    # Strategy: group faculty by (course_code, semester). Assign round-robin
    # to available sections for that semester.
    faculty_by_course: dict[str, list[str]] = {}  # (code, sem) -> [faculty names]
    faculty_all_courses: dict[str, list[dict]] = {}  # fac_name -> [{code, sem}]
    for fac in faculty_raw:
        name = fac.get("name", "")
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

    pg_core_name = constraints_doc.get("pg_shared_core")
    pg_core_code = name_to_code.get(pg_core_name, pg_core_name) if pg_core_name else None

    pg_pe_names = constraints_doc.get("pg_shared_pe", [])
    pg_pe_codes = [name_to_code.get(n, n) for n in pg_pe_names]

    lab_alloc = constraints_doc.get("cse_lab_allocations", [])

    # Identify section groups
    sections_3rd = [s for s in section_courses if s.startswith("3")]
    sections_4th = [s for s in section_courses if s.startswith("4")]
    pg_sections = [s for s in section_courses if s.startswith("SP")]

    return {
        "section_courses": section_courses,
        "faculty_assignments": faculty_assignments,
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
def _create_variables(model, section_courses, course_info):
    """Create x1 (lecture) and x2 (tutorial/practical block) BoolVars."""
    x1 = {}
    x2 = {}

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

    return x1, x2


# -----------------------------------------------------------------------
# Solution extraction
# -----------------------------------------------------------------------
def _extract_solution(solver, x1, x2, section_courses, course_info,
                      faculty_assignments):
    """
    Read solved variable values and build timetable grids.

    Returns:
        section_timetables: {section: [[cell, ...] * 7] * 5}
        faculty_timetables: {faculty: [[cell, ...] * 7] * 5}
    """
    # Section timetables
    section_tt = {}
    for sec in section_courses:
        grid = [["" for _ in range(NUM_SLOTS)] for _ in range(NUM_DAYS)]
        for cc in section_courses[sec]:
            info = course_info.get(cc, {})
            name = info.get("course_name", cc)
            # Lectures
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1 and solver.Value(x1[key]) == 1:
                        grid[d][t] = f"{cc}\n({name})\n[L]"
            # Blocks
            for d in range(NUM_DAYS):
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2 and solver.Value(x2[key]) == 1:
                            label = "Tutorial" if etype == "T" else "Practical"
                            short = "T" if etype == "T" else "P"
                            grid[d][t] = f"{cc}\n({name})\n[{short}]"
                            grid[d][t + 1] = f"{cc}\n({name})\n[{short}]"
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
    x1, x2 = _create_variables(model, section_courses, course_info)

    # Hard constraints
    add_no_faculty_clash(model, x1, x2, faculty_assignments)
    add_no_section_clash(model, x1, x2, section_courses)
    add_weekly_hours(model, x1, x2, section_courses, course_info)
    add_morning_filled(model, x1, x2, section_courses)
    add_faculty_break(model, x1, x2, faculty_assignments)

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
        add_maths_locks(model, x1, mappings["maths_slots"])

    # CSE lab locks (returns blocked room-slots, useful for future room modeling)
    _blocked = add_cse_lab_locks(model, x1, x2, mappings["lab_alloc"])

    # Soft constraints (objective)
    penalties = add_spread_penalty(model, x1, x2, section_courses)
    if penalties:
        model.Minimize(sum(penalties))

    # --- Solve ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 8  # use multiple cores

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
        sec_tt, fac_tt = _extract_solution(
            solver, x1, x2, section_courses, course_info, faculty_assignments
        )
        result["timetables"] = sec_tt
        result["faculty_timetables"] = fac_tt
    elif status_code == cp_model.INFEASIBLE:
        result["errors"].append(
            "Model is INFEASIBLE — the constraints are contradictory. "
            "Check if the weekly hours fit in the available slots and "
            "there are enough faculty members."
        )

    return result
