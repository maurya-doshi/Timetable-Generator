"""
preflight.py -- Pre-solve feasibility checker.

Catches structural issues *before* launching the expensive CP-SAT solver,
so the user gets fast, actionable feedback.

Note on scope
-------------
Faculty workload cap violations are intentionally NOT flagged here.
The input Excel may legitimately show a faculty assigned to more
theoretical hours than their designation cap. The solver's H16 constraint
handles this by balancing assignments across sections. Flagging it here
would produce false alarms. Workload analysis is done post-solve in the
workload summary, and in the IIS diagnostic if the model is INFEASIBLE.

Checks performed
----------------
1. Section event overload  -- total events for a section > 35 slots/week
2. Maths lock duplicates   -- same (Class, Day, Slot) entry appears twice
3. Missing faculty         -- a (section, course) pair has no faculty assigned
"""

from engine.constraints import NUM_DAYS, NUM_SLOTS

_AVAILABLE_SLOTS = NUM_DAYS * NUM_SLOTS  # 35


# ---------------------------------------------------------------------------
# Core check (pure function -- no DB access)
# ---------------------------------------------------------------------------

def run_preflight(
    section_courses: dict,
    course_info: dict,
    faculty_assignments: dict,
    maths_slots: list,
) -> dict:
    """Run pre-solve sanity checks.

    Parameters
    ----------
    section_courses   : {section -> [course_codes]}
    course_info       : {course_code -> {L, T, P, ...}}
    faculty_assignments : {faculty_name -> [(section, course_code)]}
    maths_slots       : list of {Class, Day, Slot, Faculty} dicts

    Returns
    -------
    {"errors": [...], "warnings": [...], "ok": bool}
        ``ok`` is True only when there are no errors (warnings are allowed).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. Section event overload
    # ------------------------------------------------------------------
    for sec, courses in section_courses.items():
        total_events = 0
        for cc in courses:
            info = course_info.get(cc, {})
            L = info.get("L", 0)
            T = info.get("T", 0)
            P = info.get("P", 0)
            # Count distinct *events* (not raw slots):
            #   L lectures + 1 tutorial block (if T>0) + 1 or 2 practical blocks
            events = (
                L
                + (1 if T > 0 else 0)
                + (2 if P == 4 else (1 if P > 0 else 0))
            )
            total_events += events

        if total_events > _AVAILABLE_SLOTS:
            errors.append(
                f"Section **{sec}**: {total_events} scheduled events exceed the "
                f"{_AVAILABLE_SLOTS} available slots/week. "
                "Reduce course hours or remove courses from this semester."
            )
        elif total_events > int(_AVAILABLE_SLOTS * 0.85):
            pct = round(100 * total_events / _AVAILABLE_SLOTS)
            warnings.append(
                f"Section **{sec}**: {total_events} events fills {pct}% of the "
                f"{_AVAILABLE_SLOTS} available weekly slots — very tight schedule."
            )

    # ------------------------------------------------------------------
    # 2. Maths lock duplicates
    # ------------------------------------------------------------------
    seen_maths: dict[tuple, bool] = {}
    for entry in maths_slots:
        cls  = entry.get("Class", "")
        day  = entry.get("Day", "")
        slot = entry.get("Slot", "")
        if not (cls and day and slot):
            continue
        key = (cls, day, slot)
        if key in seen_maths:
            errors.append(
                f"Maths conflict: Section **{cls}** has two maths entries "
                f"at **{day}, {slot}**."
            )
        seen_maths[key] = True

    # ------------------------------------------------------------------
    # 3. Courses with no faculty assigned
    # ------------------------------------------------------------------
    assigned_pairs: set[tuple] = set()
    for fac, assignments in faculty_assignments.items():
        for sec, cc in assignments:
            assigned_pairs.add((sec, cc))

    for sec, courses in section_courses.items():
        if "PG" in sec or "SP" in sec:
            continue  # PG sections may use shared faculty routing
        for cc in courses:
            if cc == "MATHS":
                continue  # MATHS handled via maths_locks, no standard faculty
            if (sec, cc) not in assigned_pairs:
                warnings.append(
                    f"No faculty assigned to **{cc}** for section **{sec}**."
                )

    return {
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }


# ---------------------------------------------------------------------------
# Convenience loader (calls solver internals)
# ---------------------------------------------------------------------------

def load_and_run(semester: str, section_map: dict | None = None) -> dict:
    """Load data from MongoDB and run the preflight checks.

    Imports solver internals to reuse the same data-loading and mapping
    logic, avoiding duplication.

    Parameters
    ----------
    semester    : "odd" or "even"
    section_map : optional override for the semester->sections mapping

    Returns
    -------
    Same dict as ``run_preflight``.
    """
    try:
        from engine.solver import _load_data, _build_mappings
        data     = _load_data(semester)
        mappings = _build_mappings(
            data["course_info"],
            data["faculty_raw"],
            data["constraints_doc"],
            section_map=section_map,
        )
        return run_preflight(
            section_courses=mappings["section_courses"],
            course_info=data["course_info"],
            faculty_assignments=mappings["faculty_assignments"],
            maths_slots=mappings["maths_slots"],
        )
    except Exception as exc:
        return {
            "errors": [f"Preflight could not run: {exc}"],
            "warnings": [],
            "ok": False,
        }
