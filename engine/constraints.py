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
NUM_SLOTS = 7
MORNING_SLOTS = [0, 1, 2, 3]          # S1-S4 must always be filled
AFTERNOON_SLOTS = [4, 5, 6]           # S5-S7
# Valid start-slots for 2-consecutive-slot blocks (can't span lunch S4→S5)
VALID_BLOCK_STARTS = [0, 2, 4, 5]  # pairs: S1-S2 (0,1), S3-S4 (2,3), S5-S6 (4,5), S6-S7 (5,6)


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
                    iv = model.NewOptionalFixedSizeIntervalVar(
                        t, 2, x1[key],
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
                        iv = model.NewOptionalFixedSizeIntervalVar(
                            t, 3, x2[key],
                            f"iv_fac_{etype}_{fac}_{sec}_{cc}_d{d}_t{t}"
                        )
                        intervals_by_fac_day[(fac, d)].append(iv)

    # Co-faculty practical blocks (2-slot) → padded to duration 3
    for (fac_name, sec, cc, d, t_start), var in co_fac.items():
        iv = model.NewOptionalFixedSizeIntervalVar(
            t_start, 3, var,
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

    all_faculty_events = []
    total_upper_events = 0

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

        all_faculty_events.extend(events)
        total_upper_events += max_events

    # Global redundant cut: helps solver prove bounds faster
    if all_faculty_events:
        model.Add(sum(all_faculty_events) <= total_upper_events)



# ===================================================================
# H2 — No section double-booking
# ===================================================================
def add_no_section_clash(model, x1, x2, section_courses):
    """
    For each section, for each day: no two courses may occupy the same time slot.

    Uses NewOptionalFixedSizeIntervalVar + AddNoOverlap — one constraint per
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
                        iv = model.NewOptionalFixedSizeIntervalVar(
                            t, 1, x1[key],
                            f"iv_sec_L_{sec}_{cc}_d{d}_t{t}"
                        )
                        intervals_by_sec_day[(sec, d)].append(iv)

                # 2-slot blocks
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        key = (sec, cc, etype, d, t)
                        if key in x2:
                            iv = model.NewOptionalFixedSizeIntervalVar(
                                t, 2, x2[key],
                                f"iv_sec_{etype}_{sec}_{cc}_d{d}_t{t}"
                            )
                            intervals_by_sec_day[(sec, d)].append(iv)

    for (sec, d), ivs in intervals_by_sec_day.items():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)



# ===================================================================
# H3 — Correct weekly hours
# ===================================================================
def add_weekly_hours(model, x1, x2, section_courses, course_info):
    """
    For each (section, course):
        sum of lecture vars == L
        sum of tutorial block vars == T
        sum of practical block vars == P

    course_info: dict  course_code -> {"L": int, "T": int, "P": int}
    """
    for sec, courses in section_courses.items():
        for cc in courses:
            info = course_info.get(cc, {})
            L = info.get("L", 0)
            T = info.get("T", 0)
            P = info.get("P", 0)

            # Lectures
            lec_vars = [x1[(sec, cc, d, t)]
                        for d in range(NUM_DAYS)
                        for t in range(NUM_SLOTS)
                        if (sec, cc, d, t) in x1]
            if lec_vars:
                model.Add(sum(lec_vars) == L)
            elif L > 0:
                # No vars created but hours required → infeasible signal
                model.Add(0 == L)  # will make model infeasible

            # Tutorials
            tut_vars = [x2[(sec, cc, "T", d, t)]
                        for d in range(NUM_DAYS)
                        for t in VALID_BLOCK_STARTS
                        if (sec, cc, "T", d, t) in x2]
            if tut_vars:
                model.Add(sum(tut_vars) == T)
            elif T > 0:
                model.Add(0 == T)

            # Practicals
            prac_vars = [x2[(sec, cc, "P", d, t)]
                         for d in range(NUM_DAYS)
                         for t in VALID_BLOCK_STARTS
                         if (sec, cc, "P", d, t) in x2]
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
    preventing any gaps in the student's schedule, while allowing them to finish early
    if they run out of weekly hours.

    The lunch break boundary (S4 → S5, i.e. t=3 → t=4) is intentionally exempt:
    a section may have morning classes only and no afternoon classes, which is valid.
    We still enforce contiguity within the morning block (S1–S4) and within the
    afternoon block (S5–S7) separately.

    slot_coverage_sec: precomputed dict (sec, d, t) -> list of BoolVars covering slot t
                       (includes x1 at t, x2 blocks starting at t, and x2 blocks starting at t-1).
    """
    LUNCH_BOUNDARY = 3  # implication active[4] => active[3] is skipped

    for sec in section_courses:
        for d in range(NUM_DAYS):
            active = []
            for t in range(NUM_SLOTS):
                terms = slot_coverage_sec.get((sec, d, t), [])
                is_active = model.NewBoolVar(f"active_{sec}_d{d}_t{t}")
                if terms:
                    model.AddMaxEquality(is_active, terms)
                else:
                    model.Add(is_active == 0)
                active.append(is_active)

            # Enforce contiguity, but SKIP the lunch boundary (S4->S5)
            for t in range(NUM_SLOTS - 1):
                if t == LUNCH_BOUNDARY:
                    continue  # S5 may be empty even if S4 is occupied
                model.AddImplication(active[t + 1], active[t])




# ===================================================================
# H4.5 — Morning-first: ALL morning slots (S1-S4) must be filled every day
# ===================================================================
def add_morning_first(model, section_courses, slot_coverage_sec):
    """
    Hard constraint: For every UG section on every day (Mon-Fri), each of the
    4 morning slots (S1-S4, indices 0-3) MUST have a class. This is
    unconditional — morning is always fully occupied. Afternoon slots are
    only used for overflow once all 20 morning slots/week are taken.

    slot_coverage_sec: precomputed dict (sec, d, t) -> list of BoolVars covering slot t.
    """
    for sec in section_courses:
        # PG sections have fewer total course-hours than UG sections and
        # cannot reliably fill all 4 morning slots every day — exempt them.
        if "PG" in sec or "SP" in sec:
            continue  # EXEMPT PG sections from mandatory morning fill
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d == 4:
                continue  # EXEMPT 7th sem from Friday classes
            for t in MORNING_SLOTS:  # [0, 1, 2, 3]
                terms = slot_coverage_sec.get((sec, d, t), [])
                if terms:
                    model.Add(sum(terms) >= 1)



# ===================================================================
# H4.6 — No empty days (every day must have at least one class)
# ===================================================================
def add_no_empty_days(model, section_courses, event_vars_sec):
    """
    For every section, every day (Monday–Friday) must have at least one
    teaching event (lecture, tutorial, or practical). No day can be blank.

    event_vars_sec: precomputed dict (sec, d) -> list of distinct event BoolVars
                    (each x1/x2 variable counted once — no double-counting for 2-slot blocks).
    """
    for sec in section_courses:
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d == 4:
                continue  # EXEMPT 7th sem from Friday classes
            terms = event_vars_sec.get((sec, d), [])
            if terms:
                model.Add(sum(terms) >= 1)


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
def add_oe_concurrency(model, x1, section_courses, oe_course_codes):
    """
    For each OE course, lock its lectures to exactly Monday, Tuesday, 
    and Wednesday at Slot 5 (1:45 - 2:40).
    """
    # Days: Mon=0, Tue=1, Wed=2. Slot 5 is index 4.
    target_slots = [(0, 4), (1, 4), (2, 4)]
    
    for cc in oe_course_codes:
        for sec, courses in section_courses.items():
            if cc in courses:
                # 1. Force the target slots to be 1 (scheduled)
                for d, t in target_slots:
                    key = (sec, cc, d, t)
                    if key in x1:
                        model.Add(x1[key] == 1)
                        
                # 2. Force all other slots to be 0 (not scheduled)
                for d in range(NUM_DAYS):
                    for t in range(NUM_SLOTS):
                        if (d, t) not in target_slots:
                            key = (sec, cc, d, t)
                            if key in x1:
                                model.Add(x1[key] == 0)


# ===================================================================
# H7 — AEC concurrency (3rd/4th sem at same day+slot)
# ===================================================================
def add_aec_concurrency(model, x1, section_courses, aec_course_codes, sections_3rd, sections_4th):
    """
    All AEC-tagged courses for 3rd & 4th semester sections are scheduled at
    the exact same (day, slot). We create auxiliary vars for the shared slot
    and tie every section's AEC lecture to it.
    """
    aec_sections = sections_3rd + sections_4th
    for cc in aec_course_codes:
        # Find sections that have this AEC course
        relevant = [s for s in aec_sections if cc in section_courses.get(s, [])]
        if len(relevant) <= 1:
            continue
        # Create one shared (day, slot) indicator
        shared = {}
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                shared[(d, t)] = model.NewBoolVar(f"aec_{cc}_d{d}_t{t}")
        # Exactly one shared slot
        model.Add(sum(shared.values()) == 1)
        # Tie each section to the shared slot
        for sec in relevant:
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    key = (sec, cc, d, t)
                    if key in x1:
                        model.Add(x1[key] == shared[(d, t)])


# ===================================================================
# H8 — PG shared classes
# ===================================================================
def add_pg_shared(model, x1, x2, section_courses, pg_sections, 
                  shared_core_code, pg_elective_codes):
    """
    Synchronizes the timetable for the two PG Specializations (SP1, SP2):
    1. Core Theory: Both sections take the Shared Core at the exact same time.
    2. Electives (PE): Both sections take their Professional Electives concurrently.
    3. Blocks (Labs/Tuts): Both sections take ALL their Labs/Tutorials concurrently.
    """
    if len(pg_sections) < 2:
        return

    s1, s2 = pg_sections[0], pg_sections[1]
    
    # 1. Sync Shared Core Lecture
    if shared_core_code and shared_core_code in section_courses.get(s1, []) and shared_core_code in section_courses.get(s2, []):
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                k1 = (s1, shared_core_code, d, t)
                k2 = (s2, shared_core_code, d, t)
                if k1 in x1 and k2 in x1:
                    model.Add(x1[k1] == x1[k2])
                    
    # 2. Sync Professional Electives (PEs)
    # The user rule: PEs occur at the exact same time. 
    # Since PE subject codes are identical in the Excel sheet for both sections, we just tie them!
    if pg_elective_codes:
        for cc in pg_elective_codes:
            if cc in section_courses.get(s1, []) and cc in section_courses.get(s2, []):
                for d in range(NUM_DAYS):
                    for t in range(NUM_SLOTS):
                        k1 = (s1, cc, d, t)
                        k2 = (s2, cc, d, t)
                        if k1 in x1 and k2 in x1:
                            model.Add(x1[k1] == x1[k2])
                            
    # 3. Sync ALL Labs and Tutorials
    # The user rule: Whenever SP-1 has ANY Lab/Tut, SP-2 MUST have a Lab/Tut at the exact same time.
    for d in range(NUM_DAYS):
        for t in VALID_BLOCK_STARTS:
            for etype in ("T", "P"):
                # Gather all block vars for SP-1 starting at (d,t)
                s1_blocks = []
                for cc in section_courses.get(s1, []):
                    k1 = (s1, cc, etype, d, t)
                    if k1 in x2:
                        s1_blocks.append(x2[k1])
                        
                # Gather all block vars for SP-2 starting at (d,t)
                s2_blocks = []
                for cc in section_courses.get(s2, []):
                    k2 = (s2, cc, etype, d, t)
                    if k2 in x2:
                        s2_blocks.append(x2[k2])
                        
                # If both have potential blocks of this type at this time, tie their sum!
                # sum(s1_blocks) == sum(s2_blocks)
                if s1_blocks and s2_blocks:
                    model.Add(sum(s1_blocks) == sum(s2_blocks))


# ===================================================================
# H9 — Maths manual slot locks
# ===================================================================
SLOT_LABEL_TO_IDX = {
    "S1 (9:00 - 9:55)": 0, "S2 (9:55 - 10:50)": 1,
    "S3 (11:05 - 12:00)": 2, "S4 (12:00 - 12:50)": 3,
    "S5 (1:45 - 2:40)": 4, "S6 (2:40 - 3:35)": 5,
    "S7 (3:35 - 4:30)": 6,
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
def add_spread_constraint(model, section_courses, course_day_events):
    """
    HARD constraint: at most 1 lecture/block of the same subject per (section, day).
    This guarantees subjects are spread across different days of the week.

    course_day_events: precomputed dict (sec, cc, d) -> list of distinct event BoolVars
                       for that course on that day (x1 and x2 each counted once).
    """
    for sec, courses in section_courses.items():
        for cc in courses:
            for d in range(NUM_DAYS):
                day_vars = course_day_events.get((sec, cc, d), [])
                if len(day_vars) >= 2:
                    model.Add(sum(day_vars) <= 1)



# ===================================================================
# S2 — No subject repeated in S1 (first slot) across the week (hard)
# ===================================================================
def add_first_slot_constraint(model, x1, x2, section_courses):
    """
    HARD constraint: no subject can occupy slot S1 (t=0, 9:00 AM)
    on more than one day in the week.
    """
    for sec, courses in section_courses.items():
        for cc in courses:
            s1_vars = []
            for d in range(NUM_DAYS):
                k1 = (sec, cc, d, 0)
                if k1 in x1:
                    s1_vars.append(x1[k1])
                for etype in ("T", "P"):
                    k2 = (sec, cc, etype, d, 0)
                    if k2 in x2:
                        s1_vars.append(x2[k2])

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
                            
            # Tutorials in lab
            if info.get("tutorial_in_lab", "No").lower() in ("yes", "y", "true"):
                T = info.get("T", 0)
                if T > 0:
                    for d in range(NUM_DAYS):
                        for t in VALID_BLOCK_STARTS:
                            k = (sec, cc, "T", d, t)
                            if k in x2:
                                needs_room.append((sec, cc, "T", d, t, 2, x2[k]))
                                
            # AEC lectures in lab
            if info.get("aec", "No").lower() in ("yes", "y", "true"):
                L = info.get("L", 0)
                if L > 0:
                    for d in range(NUM_DAYS):
                        for t in range(NUM_SLOTS):
                            k = (sec, cc, d, t)
                            if k in x1:
                                needs_room.append((sec, cc, "L", d, t, 1, x1[k]))

    # Create room-assignment BoolVars
    lab_room = {}
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        for room in LAB_ROOMS:
            var = model.NewBoolVar(f"room_{sec}_{cc}_{etype}_d{d}_t{t}_{room}")
            lab_room[(sec, cc, etype, d, t, room)] = var

    # Constraint 1 & 2: scheduled ↔ exactly 1 room
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        room_vars = [lab_room[(sec, cc, etype, d, t, room)] for room in LAB_ROOMS]
        # sum(room_vars) == active_var  (1 if scheduled, 0 if not)
        model.Add(sum(room_vars) == active_var)

    # Constraint 3: no room double-booking via AddNoOverlap
    # Each (event, room) pair becomes an optional interval; AddNoOverlap on intervals
    # grouped by (room, day) prevents any two events from sharing a room at the same time.
    room_intervals = defaultdict(list)   # (room, d) -> list of OptionalIntervalVar
    for (sec, cc, etype, d, t, duration, active_var) in needs_room:
        for room in LAB_ROOMS:
            room_var = lab_room.get((sec, cc, etype, d, t, room))
            if room_var is None:
                continue
            iv = model.NewOptionalFixedSizeIntervalVar(
                t, duration, room_var,
                f"iv_room_{room}_{sec}_{cc}_{etype}_d{d}_t{t}"
            )
            room_intervals[(room, d)].append(iv)

    for (room, d), ivs in room_intervals.items():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # Constraint 4: blocked rooms from 1st/2nd sem CSE lab locks
    for (room, d, t) in blocked_room_slots:
        if room not in LAB_ROOMS:
            continue
        # Block any assignment that would cover this (room, d, t)
        for (sec, cc, etype, d2, t2, duration, active_var) in needs_room:
            if d2 != d:
                continue
            covers = False
            if duration == 1 and t2 == t:
                covers = True
            elif duration == 2 and (t2 == t or t2 + 1 == t):
                covers = True
                
            if covers:
                k_room = (sec, cc, etype, d, t2, room)
                if k_room in lab_room:
                    model.Add(lab_room[k_room] == 0)

    # --- ROOM SYMMETRY BREAKING ---
    # To reduce the search space, if multiple rooms are available at a given time,
    # force the solver to fill them in order (Room 1, then Room 2, etc.)
    # This prevents the solver from exploring identical permutations of room assignments.
    for d in range(NUM_DAYS):
        for t in range(NUM_SLOTS):
            room_active = []
            for i, room in enumerate(LAB_ROOMS):
                # Is this room occupied at (d, t) by any of our scheduled events?
                room_vars = []
                for (sec, cc, etype, d2, t2, duration, active_var) in needs_room:
                    if d2 == d:
                        covers = (duration == 1 and t2 == t) or (duration == 2 and (t2 == t or t2 + 1 == t))
                        if covers:
                            rk = (sec, cc, etype, d, t2, room)
                            if rk in lab_room:
                                room_vars.append(lab_room[rk])
                
                is_used = model.NewBoolVar(f"room_used_d{d}_t{t}_{room}")
                if room_vars:
                    model.AddMaxEquality(is_used, room_vars)
                else:
                    model.Add(is_used == 0)
                room_active.append(is_used)

            # Symmetry break: room[i] used => room[i-1] used (if not blocked)
            for i in range(1, len(room_active)):
                prev_room = LAB_ROOMS[i-1]
                this_room = LAB_ROOMS[i]
                # Only apply if neither room is blocked by a 1st/2nd sem lock
                if (prev_room, d, t) not in blocked_room_slots and (this_room, d, t) not in blocked_room_slots:
                    model.AddImplication(room_active[i], room_active[i-1])

    return lab_room

def add_friday_half_day(model, x1, x2, section_courses):
    """
    Friday (Day 4) slots S5, S6, S7 (Slots 4, 5, 6) must be completely empty.
    For 7th Semester sections, the ENTIRE Friday is empty.
    """
    DAY_FRI = 4
    for sec, courses in section_courses.items():
        is_7th_sem = sec.startswith("7")
        for cc in courses:
            slots_to_block = range(NUM_SLOTS) if is_7th_sem else AFTERNOON_SLOTS
            for t in slots_to_block:
                k1 = (sec, cc, DAY_FRI, t)
                if k1 in x1:
                    model.Add(x1[k1] == 0)
                    
            if is_7th_sem:
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, DAY_FRI, t)
                        if k2 in x2:
                            model.Add(x2[k2] == 0)
            else:
                for t in [4, 5]:
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, DAY_FRI, t)
                        if k2 in x2:
                            model.Add(x2[k2] == 0)

# add_faculty_morning_penalty removed — no longer used or imported.
