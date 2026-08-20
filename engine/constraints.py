"""
constraints.py — Constraint definitions for the CP-SAT timetable model.

Every public function follows the signature:
    add_xxx(model, vars_dict, data_dict) -> None
and mutates *model* in-place by calling model.Add / model.AddBoolOr / etc.

Terminology used throughout:
    section   — e.g. "3A", "4B", "SP1"
    course    — course_code string, e.g. "24CS32"
    day       — int 0-4 (Mon-Fri)
    slot      — int 0-6 (S1-S7)
    x1        — dict of BoolVars for 1-slot lectures
    x2        — dict of BoolVars for 2-slot blocks (tutorials / practicals)
"""

from collections import defaultdict
from ortools.sat.python import cp_model

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NUM_DAYS = 5
NUM_SLOTS = 8
MORNING_SLOTS = [0, 1, 2, 3]          # S1-S4 must always be filled
AFTERNOON_SLOTS = [5, 6, 7]           # S5-S7
# Valid start-slots for 2-consecutive-slot blocks (can't span lunch breaks)
VALID_BLOCK_STARTS = [0, 2, 5, 6]  # pairs: S1-S2 (0,1), S3-S4 (2,3), S5-S6 (5,6), S6-S7 (6,7)


# ===================================================================
# H1 — No faculty double-booking
# ===================================================================
def add_no_faculty_clash(model, x1, x2, co_fac, faculty_assignments, pg_shared_core_code=None, pg_sections=None):
    """
    For each faculty member, for each day: no two teaching events may overlap AND
    every event must be followed by at least 1 free slot before the next.

    **Technique 3 — Padded intervals:**
    Instead of a separate add_faculty_break pass, all intervals are created with
    duration = (event_duration + 1). AddNoOverlap on padded intervals then enforces
    both H1 (no double-booking) and H6/H6.5 (1-slot gap between classes) in a single
    constraint per (faculty, day), replacing add_faculty_break and add_co_faculty_break.

      Lecture  (1-slot)  → padded duration 2  |S_t, S_t+1 reserved|
      Block    (2-slot)  → padded duration 3  |S_t, S_t+1, S_t+2 reserved|
      Co-fac   (2-slot)  → padded duration 3

    The "overflow" beyond the last slot is harmless: no interval starts past S7.

    PG Shared Core deduplication: SP-1 and SP-2 sit in the same room with the
    same teacher, so only one interval is created per (faculty, course, day, slot).

    faculty_assignments: dict  faculty_name -> list of (section, course_code)
    """
    intervals_by_fac_day = defaultdict(list)   # (fac, d) -> list of OptionalIntervalVar

    for fac, assignments in faculty_assignments.items():
        seen_pg_core = set()   # (cc, d, t, etype) — dedup per faculty

        for sec, cc in assignments:
            is_pg_core = (
                pg_shared_core_code and cc == pg_shared_core_code
                and pg_sections and sec in pg_sections
            )

            for d in range(NUM_DAYS):
                # 1-slot lecture → padded to duration 2
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key not in x1:
                        continue
                    if is_pg_core:
                        dedup = (cc, d, t, "L")
                        if dedup in seen_pg_core:
                            continue
                        seen_pg_core.add(dedup)
                    iv = model.NewOptionalIntervalVar(
                        t, 2, t + 2, x1[key],
                        f"iv_fac_L_{fac}_{sec}_{cc}_d{d}_t{t}"
                    )
                    intervals_by_fac_day[(fac, d)].append(iv)

                # 2-slot block → padded to duration 3
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key not in x2:
                            continue
                        if is_pg_core:
                            dedup = (cc, d, t, etype)
                            if dedup in seen_pg_core:
                                continue
                            seen_pg_core.add(dedup)
                        iv = model.NewOptionalIntervalVar(
                            t, 3, t + 3, x2[key],
                            f"iv_fac_{etype}_{fac}_{sec}_{cc}_d{d}_t{t}"
                        )
                        intervals_by_fac_day[(fac, d)].append(iv)

    # Co-faculty practical blocks (2-slot) → padded to duration 3
    for (fac_name, sec, cc, d, t_start), var in co_fac.items():
        iv = model.NewOptionalIntervalVar(
            t_start, 3, t_start + 3, var,
            f"iv_cofac_{fac_name}_{sec}_{cc}_d{d}_t{t_start}"
        )
        intervals_by_fac_day[(fac_name, d)].append(iv)

    # One AddNoOverlap per (faculty, day):
    #   - prevents double-booking (H1 / H1.5)
    #   - the +1 padding enforces the mandatory 1-slot break (H6 / H6.5)
    for (fac, d), ivs in intervals_by_fac_day.items():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)



# ===================================================================
# H1.5 — Dynamic Co-Faculty Logic & Workload Caps
# ===================================================================
def add_co_faculty_logic(model, x2, co_fac, faculty_assignments):
    """
    For every practical (P) block, exactly 2 Co-Faculty members must be assigned.

    Note: Primary faculty are already excluded at variable-creation time
    (_create_variables only creates co_fac vars for non-primary faculty),
    so no explicit exclusion constraint is needed here.
    """
    block_to_cofacs = defaultdict(list)
    for (fac_name, sec, cc, d, t), var in co_fac.items():
        block_to_cofacs[(sec, cc, d, t)].append((fac_name, var))

    for (sec, cc, d, t), cofac_list in block_to_cofacs.items():
        k_prac = (sec, cc, "P", d, t)
        if k_prac in x2:
            prac_var = x2[k_prac]

            # Exactly 2 co-faculty if the block is scheduled, 0 otherwise.
            all_vars = [var for _, var in cofac_list]
            model.Add(sum(all_vars) == 2 * prac_var)


def add_max_workload(model, co_fac, faculty_assignments, faculty_designations,
                     events_by_fac, semester="odd", count_cofac_in_workload=None):
    """
    Enforces a per-faculty workload cap based on designation.

    Unit rule
    ---------
      1 lecture slot       (L) = 1 event
      1 tutorial block     (T) = 1 event  (2 slots counted as one block)
      1 practical block    (P) = 1 event  (2 slots counted as one block)
      1 co-faculty lab block   = 1 event  (always included)

    Max events = max_units // 2
    ---------
      Odd  semester : Professor=18, Associate=24, Assistant=28  (units)
      Even semester : Professor=14, Associate=18, Assistant=24  (units)

    Only an upper cap is enforced. A lower bound is intentionally omitted
    because faculty may legitimately teach fewer classes than the target
    (e.g. they only appear in the DB for one course).

    The `count_cofac_in_workload` parameter is kept for API compatibility
    but is no longer used — co-faculty blocks always count toward the cap.

    events_by_fac: precomputed dict  faculty_name -> list of primary BoolVars
                   (x1 + x2 vars for all (section, course) in that faculty's assignments,
                    each var counted once). Co-faculty vars are added separately below.
    """
    if semester.lower() == "odd":
        caps = {"Professor": 18, "Associate": 24, "Assistant": 28}
    else:
        caps = {"Professor": 14, "Associate": 18, "Assistant": 24}

    cofac_by_fac = defaultdict(list)
    for (fac_name, sec, cc, d, t), var in co_fac.items():
        cofac_by_fac[fac_name].append(var)

    for fac, assignments in faculty_assignments.items():
        desig = faculty_designations.get(fac, "Assistant")
        max_units = caps.get(desig, 28)
        max_events = max_units // 2   # e.g. 28 units → 14 events

        # Primary events from precomputed map + co-faculty blocks
        events = list(events_by_fac.get(fac, []))
        events.extend(cofac_by_fac.get(fac, []))

        if not events:
            continue

        # Upper cap: faculty cannot exceed their designation limit
        model.Add(sum(events) <= max_events)



# ===================================================================
# H2 — No section double-booking
# ===================================================================
def add_no_section_clash(model, x1, x2, section_courses):
    """
    For each section, for each day: no two courses may occupy the same time slot.

    Uses NewOptionalIntervalVar + AddNoOverlap — one constraint per
    (section, day) instead of one sum() <= 1 per (section, day, slot).

    section_courses: dict  section -> list of course_codes
    """
    intervals_by_sec_day = defaultdict(list)   # (sec, d) -> list of OptionalIntervalVar

    for sec, courses in section_courses.items():
        for cc in courses:
            for d in range(NUM_DAYS):
                # 1-slot lectures
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1:
                        iv = model.NewOptionalIntervalVar(
                            t, 1, t + 1, x1[key],
                            f"iv_sec_L_{sec}_{cc}_d{d}_t{t}"
                        )
                        intervals_by_sec_day[(sec, d)].append(iv)

                # 2-slot blocks
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2:
                            iv = model.NewOptionalIntervalVar(
                                t, 2, t + 2, x2[key],
                                f"iv_sec_{etype}_{sec}_{cc}_d{d}_t{t}"
                            )
                            intervals_by_sec_day[(sec, d)].append(iv)

    for (sec, d), ivs in intervals_by_sec_day.items():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)



# ===================================================================
# H3 — Correct weekly hours
# ===================================================================
def add_weekly_hours(model, section_courses, course_info,
                     x1_by_sec_cc, x2T_by_sec_cc, x2P_by_sec_cc):
    """
    For each (section, course):
        sum of lecture vars == L
        sum of tutorial block vars == T
        sum of practical block vars == P

    Uses precomputed index maps — no inner loops over (day × slot) keys.
      x1_by_sec_cc[(sec, cc)]  → all lecture BoolVars
      x2T_by_sec_cc[(sec, cc)] → all tutorial block BoolVars
      x2P_by_sec_cc[(sec, cc)] → all practical block BoolVars

    course_info: dict  course_code → {"L": int, "T": int, "P": int}
    """
    for sec, courses in section_courses.items():
        for cc in courses:
            info = course_info.get(cc, {})
            L = info.get("L", 0)
            T = info.get("T", 0)
            P = info.get("P", 0)

            # Lectures
            lec_vars = x1_by_sec_cc.get((sec, cc), [])
            if lec_vars:
                model.Add(sum(lec_vars) == L)
            elif L > 0:
                model.Add(0 == L)  # no vars but hours required → infeasible signal

            # Tutorials
            tut_vars = x2T_by_sec_cc.get((sec, cc), [])
            if tut_vars:
                model.Add(sum(tut_vars) == T)
            elif T > 0:
                model.Add(0 == T)

            # Practicals
            prac_vars = x2P_by_sec_cc.get((sec, cc), [])
            if prac_vars:
                model.Add(sum(prac_vars) == P)
            elif P > 0:
                model.Add(0 == P)


# ===================================================================
# H4 — No Student Gaps (Contiguous from S1)
# ===================================================================
def add_no_student_gaps(model, section_courses, slot_coverage_sec):
    """
    Ensures that if a section has a class at slot t, they MUST have a class at slot t-1.
    This forces all classes to be packed at the start of the day (S1 onwards),
    preventing any gaps in the student's schedule.

    The lunch break boundary (S4 → S5, i.e. t=3 → t=4) is intentionally exempt:
    a section may have morning classes only and no afternoon classes, which is valid.

    slot_coverage_sec: precomputed dict (sec, d, t) → list of BoolVars covering slot t.
    """
    LUNCH_BOUNDARY = 3

    for sec in section_courses:
        # 1st sem sections only exist for faculty blocking — no class timetable
        if sec.startswith("1"):
            continue
        for d in range(NUM_DAYS):
            # active[t] is a BoolVar if the slot has coverage vars, else None.
            # None means the slot is provably always empty — no aux var is created
            # and no forced-zero constraint is added.
            active = []
            for t in range(NUM_SLOTS):
                terms = slot_coverage_sec.get((sec, d, t), [])
                if terms:
                    is_active = model.NewBoolVar(f"active_{sec}_d{d}_t{t}")
                    model.AddMaxEquality(is_active, terms)
                else:
                    is_active = None   # always empty — skip var creation
                active.append(is_active)
            
            # For 3rd, 5th, 7th sem, L1 (slot 4) is their fixed lunch break.
            if active[4] is not None:
                model.Add(active[4] == 0)

            for t in range(1, NUM_SLOTS):
                a_curr = active[t]
                if a_curr is None:
                    continue
                    
                # Determine the logical previous slot
                if t == 5:
                    a_prev = active[3]  # S5 implies S4 (skipping L1 lunch)
                elif t == 4:
                    continue            # L1 is always empty for solver sections
                else:
                    a_prev = active[t - 1]
                
                if a_prev is None:
                    model.Add(a_curr == 0)
                else:
                    model.AddImplication(a_curr, a_prev)




# ===================================================================
# H4.5 — Morning-first: ALL morning slots (S1-S4) must be filled every day (hard)
# ===================================================================
def add_morning_first(model, section_courses, slot_coverage_sec):
    """
    HARD constraint: For every UG section on every day (Mon-Fri), each of the
    4 morning slots (S1-S4, indices 0-3) MUST have a class.
    """
    for sec in section_courses:
        if "PG" in sec or "SP" in sec or sec.startswith("1"):
            continue
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d >= 3:
                continue
            for t in MORNING_SLOTS:  # [0, 1, 2, 3]
                terms = slot_coverage_sec.get((sec, d, t), [])
                if terms:
                    model.Add(sum(terms) >= 1)
                else:
                    model.AddBoolOr([]) # force infeasible if slot has no possible classes



# ===================================================================
# H4.6 — No empty days (every day must have at least one class)
# ===================================================================
def add_no_empty_days(model, section_courses, event_vars_sec, penalty_weight=500):
    """
    SOFT constraint: every section should have at least one teaching event
    each day. Violations incur a high penalty in the objective.

    Returns a list of penalty terms to include in model.Minimize().
    """
    penalties = []
    for sec in section_courses:
        if sec.startswith("1"):
            continue
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d >= 3:
                continue
            terms = event_vars_sec.get((sec, d), [])
            if terms:
                is_empty = model.NewBoolVar(f"empty_day_{sec}_d{d}")
                model.Add(sum(terms) >= 1).OnlyEnforceIf(is_empty.Not())
                model.Add(sum(terms) == 0).OnlyEnforceIf(is_empty)
                penalties.append(penalty_weight * is_empty)
    return penalties


# ===================================================================
# H6 — Faculty Break (merged into add_no_faculty_clash via padded intervals)
# ===================================================================
def add_faculty_break(model, x1, x2, faculty_assignments, co_fac=None):
    """
    DEPRECATED — no longer called.

    H6 (1-slot faculty break between consecutive classes) is now enforced
    automatically by the padded interval durations in add_no_faculty_clash:
      - lecture duration 1 → padded 2  (reserves the next slot)
      - block   duration 2 → padded 3  (reserves the slot after the block)
    AddNoOverlap on those padded intervals subsumes both H1 and H6.
    """
    pass


# ===================================================================
# H6.5 — Co-faculty break (merged into add_no_faculty_clash via padded intervals)
# ===================================================================
def add_co_faculty_break(model, x1, x2, co_fac, faculty_assignments):
    """
    DEPRECATED — no longer called.

    H6.5 (1-slot gap between primary events and co-faculty blocks, and between
    consecutive co-faculty duties) is now fully enforced by the padded interval
    durations in add_no_faculty_clash. Co-faculty intervals use duration=3
    (2-slot block + 1-slot padding), so AddNoOverlap automatically enforces:
      Rule A) primary ends at t → no co-fac starts at t+1
      Rule B) co-fac ends at t+1 → no primary starts at t+2
      Rule C) no two co-fac duties back-to-back without a gap
    """
    pass


# ===================================================================
# H6 — OE concurrency (all sections take each OE at the same time)
# ===================================================================
def add_oe_concurrency(model, section_courses, oe_course_codes, x1_keys_by_sec_cc):
    """
    Ensure all sections of the same semester take their OE lectures concurrently.
    The solver freely picks which (day, slot) combinations to use — no fixed slot.
    Shared BoolVars guarantee that if section 5A has an OE lecture on Monday S2,
    then 5B/5C/5D must also have it on Monday S2.

    x1_keys_by_sec_cc: precomputed dict (sec, cc) → list of (d, t, var).
    """
    for cc in oe_course_codes:
        relevant_secs = [sec for sec, courses in section_courses.items() if cc in courses]
        if len(relevant_secs) <= 1:
            continue

        # Only consider (d,t) pairs available to ALL sections
        dt_per_sec = {}
        for sec in relevant_secs:
            sec_dts = set()
            for d, t, _ in x1_keys_by_sec_cc.get((sec, cc), []):
                sec_dts.add((d, t))
            dt_per_sec[sec] = sec_dts

        common_dt = set.intersection(*dt_per_sec.values()) if dt_per_sec else set()
        if not common_dt:
            continue

        # Create shared BoolVars — solver picks which slots to use
        shared = {(d, t): model.NewBoolVar(f"oe_{cc}_d{d}_t{t}") for (d, t) in common_dt}

        # Force each section's x1 vars to match the shared schedule
        for sec in relevant_secs:
            for d, t, var in x1_keys_by_sec_cc.get((sec, cc), []):
                if (d, t) in shared:
                    model.Add(var == shared[(d, t)])
                else:
                    model.Add(var == 0)  # slot not common to all sections


# ===================================================================
# H7 — AEC concurrency (locked to S5 Mon/Tue/Wed)
# ===================================================================
def add_aec_concurrency(model, section_courses, aec_course_codes, sections_3rd, sections_4th,
                         x1_keys_by_sec_cc):
    """
    Lock AEC courses to S5 (slot 5) on Monday, Tuesday, and Wednesday.
    All relevant 3rd & 4th semester sections take AECs concurrently within
    those fixed slots. Non-target slots are zeroed out; shared BoolVars
    ensure cross-section concurrency within the target window.

    x1_keys_by_sec_cc: precomputed dict (sec, cc) → list of (d, t, var).
    """
    target_set = {(0, 5), (1, 5), (2, 5)}  # Mon/Tue/Wed at S5
    aec_sections = sections_3rd + sections_4th

    for cc in aec_course_codes:
        relevant = [s for s in aec_sections if cc in section_courses.get(s, [])]
        if not relevant:
            continue

        # Force all non-target slots to 0 — AECs can only be at S5 Mon/Tue/Wed
        for sec in relevant:
            for d, t, var in x1_keys_by_sec_cc.get((sec, cc), []):
                if (d, t) not in target_set:
                    model.Add(var == 0)

        # If only 1 section, slot restriction above is sufficient
        if len(relevant) <= 1:
            continue

        # Shared BoolVars for target slots — forces all sections to agree
        valid_targets = set()
        for sec in relevant:
            for d, t, _ in x1_keys_by_sec_cc.get((sec, cc), []):
                if (d, t) in target_set:
                    valid_targets.add((d, t))

        if not valid_targets:
            continue

        shared = {(d, t): model.NewBoolVar(f"aec_{cc}_d{d}_t{t}") for (d, t) in valid_targets}

        for sec in relevant:
            for d, t, var in x1_keys_by_sec_cc.get((sec, cc), []):
                if (d, t) in shared:
                    model.Add(var == shared[(d, t)])


# ===================================================================
# H8 — PG shared classes
# ===================================================================
def add_pg_shared(model, section_courses, pg_sections,
                  shared_core_code, pg_elective_codes,
                  x1_by_sec_cc, x2_by_sec_dt_etype):
    """
    Synchronizes the timetable for the two PG Specializations (SP1, SP2):
    1. Core Theory: Both sections take the Shared Core at the exact same time.
    2. Electives (PE): Both sections take their Professional Electives concurrently.
    3. Blocks (Labs/Tuts): Both sections take ALL their Labs/Tutorials concurrently.

    x1_by_sec_cc: precomputed (sec, cc) → lecture BoolVars in identical (d, t) order
                  for both PG sections (guaranteed by _create_variables iteration order).
    x2_by_sec_dt_etype: precomputed (sec, d, t, etype) → block BoolVars.
    """
    if len(pg_sections) < 2:
        return

    s1, s2 = pg_sections[0], pg_sections[1]

    # 1. Sync Shared Core Lecture — positional zip is safe: both sections have
    #    vars in identical (d=0..4, t=0..6) insertion order.
    if (shared_core_code
            and shared_core_code in section_courses.get(s1, [])
            and shared_core_code in section_courses.get(s2, [])):
        for v1, v2 in zip(x1_by_sec_cc.get((s1, shared_core_code), []),
                          x1_by_sec_cc.get((s2, shared_core_code), [])):
            model.Add(v1 == v2)

    # 2. Sync Professional Electives
    if pg_elective_codes:
        for cc in pg_elective_codes:
            if cc in section_courses.get(s1, []) and cc in section_courses.get(s2, []):
                for v1, v2 in zip(x1_by_sec_cc.get((s1, cc), []),
                                  x1_by_sec_cc.get((s2, cc), [])):
                    model.Add(v1 == v2)

    # 3. Sync ALL Labs and Tutorials per (d, t, etype) — one dict lookup per slot
    for d in range(NUM_DAYS):
        for t in VALID_BLOCK_STARTS:
            for etype in ("T", "P"):
                s1_blocks = x2_by_sec_dt_etype.get((s1, d, t, etype), [])
                s2_blocks = x2_by_sec_dt_etype.get((s2, d, t, etype), [])
                if s1_blocks and s2_blocks:
                    model.Add(sum(s1_blocks) == sum(s2_blocks))


# ===================================================================
# H9 — Maths manual slot locks
# ===================================================================
SLOT_LABEL_TO_IDX = {
    "S1 (9:00 - 9:55)": 0, "S2 (9:55 - 10:50)": 1,
    "S3 (11:05 - 12:00)": 2, "S4 (12:00 - 12:50)": 3,
    "L1 (12:50 - 1:45)": 4, "S5 (1:45 - 2:40)": 5, 
    "S6 (2:40 - 3:35)": 6, "S7 (3:35 - 4:30)": 7,
}
DAY_LABEL_TO_IDX = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5,
}


def add_maths_locks(model, x1, x2, maths_slots, maths_course_code="MATHS"):
    """
    Lock pre-assigned maths slots.
    maths_slots: list of {"Class": "3A", "Day": "Monday", "Slot": "S1 (...)", "Faculty": "MATHS TUT"}
    """
    for entry in maths_slots:
        sec = entry.get("Class", "")
        day_label = entry.get("Day", "")
        slot_label = entry.get("Slot", "")
        faculty_label = entry.get("Faculty", "")
        if not sec or not day_label or not slot_label:
            continue
        d = DAY_LABEL_TO_IDX.get(day_label)
        t = SLOT_LABEL_TO_IDX.get(slot_label)
        if d is None or t is None:
            continue
            
        if "TUT" in faculty_label.upper():
            # 2-slot tutorial block
            key2 = (sec, maths_course_code, "T", d, t)
            if key2 in x2:
                model.Add(x2[key2] == 1)
        else:
            # 1-slot lecture
            key1 = (sec, maths_course_code, d, t)
            if key1 in x1:
                model.Add(x1[key1] == 1)


# ===================================================================
# H10.5 — 1st Sem Class Blocking
# ===================================================================
def add_first_sem_blocking(model, x1, x2, first_sem_blocking):
    """
    Lock pre-assigned 1st semester subjects to specific slots.
    first_sem_blocking: list of {"Class": "1A", "Course Code": "26...", "Day": "Monday", "Slot": "S1 (...)"}
    """
    for entry in first_sem_blocking:
        sec = entry.get("Class", "")
        cc = entry.get("Course Code", "")
        day_label = entry.get("Day", "")
        slot_label = entry.get("Slot", "")
        if not sec or not cc or not day_label or not slot_label:
            continue
        d = DAY_LABEL_TO_IDX.get(day_label)
        t = SLOT_LABEL_TO_IDX.get(slot_label)
        if d is None or t is None:
            continue
            
        # 1-slot lecture
        key1 = (sec, cc, d, t)
        if key1 in x1:
            model.Add(x1[key1] == 1)
            
        # 2-slot practical/tutorial (x2 is keyed by the start slot)
        for etype in ["T", "P"]:
            key2 = (sec, cc, etype, d, t)
            if key2 in x2:
                model.Add(x2[key2] == 1)


# ===================================================================
# H10 — CSE Lab allocation locks
# ===================================================================
def add_cse_lab_locks(model, x1, x2, lab_allocations):
    """
    Lock pre-assigned CSE lab room/time for 1st/2nd sem sections.
    lab_allocations: list of {"Class":"1A","Lab Room":"CSE Lab 1","Day":...,"Slot":...}

    These sections are not part of the main solver (they are 1st/2nd sem),
    but their lab rooms become unavailable at those times for 3rd/4th sem labs.
    Returns a set of (lab_room, day, slot) tuples that are blocked.
    """
    blocked = set()
    for entry in lab_allocations:
        day_label = entry.get("Day", "")
        slot_label = entry.get("Slot", "")
        lab_room = entry.get("Lab Room", "")
        if not day_label or not slot_label or not lab_room:
            continue
        d = DAY_LABEL_TO_IDX.get(day_label)
        t = SLOT_LABEL_TO_IDX.get(slot_label)
        if d is not None and t is not None:
            blocked.add((lab_room, d, t))
    return blocked


# ===================================================================
# S1 — Spread subjects across days (soft)
# ===================================================================
def add_spread_constraint(model, section_courses, x1, x2, course_info=None):
    """
    SOFT constraint: penalize multiple lectures or multiple practical blocks of the same
    subject for the same section on the same day. Returns penalty terms to add to the objective.
    """
    from collections import defaultdict
    lecture_day_vars = defaultdict(list)
    block_day_vars = defaultdict(list)
    penalties = []
    
    for (sec, cc, d, t), var in x1.items():
        lecture_day_vars[(sec, cc, d)].append(var)
    for (sec, cc, etype, d, t_start), var in x2.items():
        block_day_vars[(sec, cc, d)].append(var)
        
    for sec, courses in section_courses.items():
        for cc in courses:
            for d in range(NUM_DAYS):
                lvars = lecture_day_vars.get((sec, cc, d), [])
                if len(lvars) >= 2:
                    is_multi = model.NewBoolVar(f"multi_lec_{sec}_{cc}_d{d}")
                    model.Add(sum(lvars) >= 2).OnlyEnforceIf(is_multi)
                    model.Add(sum(lvars) <= 1).OnlyEnforceIf(is_multi.Not())
                    penalties.append(10 * is_multi)
                bvars = block_day_vars.get((sec, cc, d), [])
                if len(bvars) >= 2:
                    is_multi_b = model.NewBoolVar(f"multi_blk_{sec}_{cc}_d{d}")
                    model.Add(sum(bvars) >= 2).OnlyEnforceIf(is_multi_b)
                    model.Add(sum(bvars) <= 1).OnlyEnforceIf(is_multi_b.Not())
                    penalties.append(10 * is_multi_b)
    return penalties



# ===================================================================
# S2 — No subject repeated in S1 (first slot) across the week (hard)
# ===================================================================
def add_first_slot_constraint(model, section_courses, x1_t0_by_sec_cc, x2_t0_by_sec_cc):
    """
    HARD constraint: no subject can occupy slot S1 (t=0, 9:00 AM)
    on more than one day in the week.

    x1_t0_by_sec_cc: precomputed (sec, cc) → lecture vars at t=0 (one per weekday max).
    x2_t0_by_sec_cc: precomputed (sec, cc) → block vars starting at t=0.
    """
    for sec, courses in section_courses.items():
        for cc in courses:
            s1_vars = (x1_t0_by_sec_cc.get((sec, cc), []) +
                       x2_t0_by_sec_cc.get((sec, cc), []))
            if len(s1_vars) >= 2:
                model.Add(sum(s1_vars) <= 1)

# ===================================================================
# Final Time-Based Constraints
# ===================================================================
LAB_ROOMS = ["CSE Lab 1", "CSE Lab 2", "CSE Lab 3", "CSE Lab 4"]


def add_lab_room_assignment(model, x1, x2, section_courses, course_info,
                            pg_sections, blocked_room_slots=None):
    """
    Assign each scheduled practical (and tutorial-in-lab) block, as well as AEC lectures,
    to exactly one of CSE Lab 1–4.  Ensures:
        1. If an event is scheduled → it gets exactly 1 room.
        2. If an event is NOT scheduled → it gets 0 rooms.
        3. No two events share the same room at the same time.
           A 2-slot block occupies slots t AND t+1. A 1-slot lecture occupies slot t.
        4. Rooms blocked by 1st/2nd sem CSE lab locks are unavailable.

    Returns:
        lab_room  — dict  (sec, cc, etype, d, t, room) → BoolVar
    """
    if blocked_room_slots is None:
        blocked_room_slots = set()

    # Collect all items that need a room.
    # We will store a tuple: (sec, cc, etype, d, t, duration, active_var)
    needs_room = []
    
    for sec, courses in section_courses.items():
        for cc in courses:
            info = course_info.get(cc, {})
            
            # Practicals always need a lab
            P = info.get("P", 0)
            if P > 0:
                for d in range(NUM_DAYS):
                    for t in VALID_BLOCK_STARTS:
                        k = (sec, cc, "P", d, t)
                        if k in x2:
                            needs_room.append((sec, cc, "P", d, t, 2, x2[k]))
                            
            # Tutorials explicitly marked as needing a computer lab
            if info.get("tutorial_in_lab", "No").lower() in ("yes", "y", "true"):
                T = info.get("T", 0)
                if T > 0:
                    for d in range(NUM_DAYS):
                        for t in VALID_BLOCK_STARTS:
                            k = (sec, cc, "T", d, t)
                            if k in x2:
                                needs_room.append((sec, cc, "T", d, t, 2, x2[k]))

    # Create room-assignment BoolVars
    lab_room = {}
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        for room in LAB_ROOMS:
            var = model.NewBoolVar(f"room_{sec}_{cc}_{etype}_d{d}_t{t}_{room}")
            lab_room[(sec, cc, etype, d, t, room)] = var

    # Constraint 1 & 2: each event gets at most 1 room (from available CSE Labs 1-4)
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        room_vars = [lab_room[(sec, cc, etype, d, t, room)] for room in LAB_ROOMS]
        model.Add(sum(room_vars) <= active_var)

    # Constraint 3: no room double-booking via AddNoOverlap
    # Each (event, room) pair becomes an optional interval; AddNoOverlap on intervals
    # grouped by (room, day) prevents any two events from sharing a room at the same time.
    room_intervals = defaultdict(list)   # (room, d) -> list of OptionalIntervalVar
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        for room in LAB_ROOMS:
            room_var = lab_room.get((sec, cc, etype, d, t, room))
            if room_var is None:
                continue
            iv = model.NewOptionalIntervalVar(
                t, duration, t + duration, room_var,
                f"iv_room_{room}_{sec}_{cc}_{etype}_d{d}_t{t}"
            )
            room_intervals[(room, d)].append(iv)

    for (room, d), ivs in room_intervals.items():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # Build slot-coverage index in one O(N) pass — used by Constraints 4 and symmetry.
    # slot_to_needs_idx[(d, t)] → indices into needs_room whose event COVERS slot t on day d.
    slot_to_needs_idx = defaultdict(list)
    for idx, (sec, cc, etype, d, t, dur, _) in enumerate(needs_room):
        slot_to_needs_idx[(d, t)].append(idx)
        if dur == 2:
            slot_to_needs_idx[(d, t + 1)].append(idx)

    # Constraint 4: blocked rooms — O(|blocked| × avg events per slot) vs old O(|blocked| × N)
    for (room, d, t) in blocked_room_slots:
        if room not in LAB_ROOMS:
            continue
        for idx in slot_to_needs_idx.get((d, t), []):
            sec, cc, etype, _, t2, _, _ = needs_room[idx]
            k_room = (sec, cc, etype, d, t2, room)
            if k_room in lab_room:
                model.Add(lab_room[k_room] == 0)

    # NOTE: Room symmetry breaking removed. When rooms are blocked by
    # 1st-sem CSE lab locks, the implication chain (room_i_used → room_{i-1}_used)
    # can propagate through blocked rooms and force other rooms to 0, creating
    # infeasibility. The solver handles room assignment well without it.

    return lab_room

def add_friday_half_day(model, x1, x2, section_courses, course_day_events):
    """
    Friday (Day 4) slots S5, S6, S7 (Slots 4, 5, 6) must be completely empty.
    For 7th Semester sections, the ENTIRE Thursday (Day 3) and Friday are empty.

    course_day_events: precomputed (sec, cc, d) → list of event BoolVars.
    Used to zero all Thursday/Friday events for 7th sem in one fast pass.
    """
    DAY_THU = 3
    DAY_FRI = 4
    for sec, courses in section_courses.items():
        is_7th_sem = sec.startswith("7")
        for cc in courses:
            if is_7th_sem:
                # Zero ALL Thursday and Friday events via precomputed lookup
                for d in [DAY_THU, DAY_FRI]:
                    for var in course_day_events.get((sec, cc, d), []):
                        model.Add(var == 0)
            else:
                # Zero Friday afternoon lecture slots
                for t in AFTERNOON_SLOTS:
                    k1 = (sec, cc, DAY_FRI, t)
                    if k1 in x1:
                        model.Add(x1[k1] == 0)
                # Zero Friday afternoon block starts (S5-S6 and S6-S7)
                for t in [5, 6]:
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, DAY_FRI, t)
                        if k2 in x2:
                            model.Add(x2[k2] == 0)


# ===================================================================
# S2 — Subject-to-Lab Room Preference (Soft Constraint)
# ===================================================================
DEFAULT_SUBJECT_LAB_PREFERENCES = [
    {"Keyword": "OOP", "Preferred Lab": "CSE Lab 1"},
    {"Keyword": "DS", "Preferred Lab": "CSE Lab 1"},
    {"Keyword": "DATA STRUCTURES", "Preferred Lab": "CSE Lab 1"},
    {"Keyword": "DDCO", "Preferred Lab": "CSE Lab 2"},
    {"Keyword": "DCN", "Preferred Lab": "CSE Lab 2"},
    {"Keyword": "NETWORKS", "Preferred Lab": "CSE Lab 2"},
    {"Keyword": "MICROSERVICE", "Preferred Lab": "CSE Lab 3"},
    {"Keyword": "FULL STACK", "Preferred Lab": "CSE Lab 3"},
    {"Keyword": "CD", "Preferred Lab": "CSE Lab 3"},
    {"Keyword": "COMPILER", "Preferred Lab": "CSE Lab 3"},
    {"Keyword": "AIML", "Preferred Lab": "CSE Lab 4"},
    {"Keyword": "SKILL", "Preferred Lab": "CSE Lab 4"},
    {"Keyword": "MAP", "Preferred Lab": "CSE Lab 4"},
]


def add_subject_lab_preferences(model, lab_room, course_info, subject_lab_prefs=None, penalty_weight=50):
    """
    Soft Constraint S2 — Preferred Lab Room Allocation.

    If a course matches a preferred CSE Lab room (e.g. OOP Lab -> CSE Lab 1),
    allocating it to a non-preferred room incurs a soft penalty in the objective.
    """
    if subject_lab_prefs is None:
        subject_lab_prefs = DEFAULT_SUBJECT_LAB_PREFERENCES

    # Normalize subject_lab_prefs into a list of (keyword_upper, preferred_room)
    rules = []
    if isinstance(subject_lab_prefs, dict):
        for room, kws in subject_lab_prefs.items():
            if isinstance(kws, list):
                for kw in kws:
                    rules.append((str(kw).strip().upper(), room))
            elif isinstance(kws, str):
                rules.append((kws.strip().upper(), room))
    elif isinstance(subject_lab_prefs, list):
        for entry in subject_lab_prefs:
            if isinstance(entry, dict):
                kw = entry.get("Keyword") or entry.get("keyword") or entry.get("Subject") or entry.get("subject")
                room = entry.get("Preferred Lab") or entry.get("preferred_lab") or entry.get("Lab Room") or entry.get("room")
                if kw and room:
                    rules.append((str(kw).strip().upper(), str(room).strip()))

    if not rules:
        return []

    # Map course_code -> preferred_room
    preferred_room_map = {}
    for code, info in course_info.items():
        name_upper = str(info.get("course_name", "")).upper()
        code_upper = str(code).upper()
        for kw_u, room in rules:
            if kw_u and (kw_u in code_upper or kw_u in name_upper):
                preferred_room_map[code] = room
                break

    penalties = []
    for (sec, cc, etype, d, t, room), var in lab_room.items():
        pref_room = preferred_room_map.get(cc)
        if pref_room and room != pref_room:
            penalties.append(penalty_weight * var)

    return penalties

