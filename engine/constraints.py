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
    For each faculty member, for each (day, slot): at most 1 teaching event.
    Deduplicates the PG Shared Core course since SP-1 and SP-2 sit in the same room.
    Also ensures they are not double-booked as a dynamic Co-Faculty.

    faculty_assignments: dict  faculty_name -> list of (section, course_code)
    """
    from collections import defaultdict
    # Build a lookup for co_fac variables by (fac_name, day, slot_it_covers)
    # Since each co_fac block is 2 slots, it covers t and t+1.
    cofac_lookup = defaultdict(list)
    for (fac_name, sec, cc, d_var, t_var), var in co_fac.items():
        cofac_lookup[(fac_name, d_var, t_var)].append(var)
        cofac_lookup[(fac_name, d_var, t_var + 1)].append(var)

    for fac, assignments in faculty_assignments.items():
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                terms = []
                # To handle PG Shared Core, we should only count it once per faculty per time slot.
                seen_pg_core_lecture = False
                seen_pg_core_blocks = set()

                for sec, cc in assignments:
                    is_pg_core = (pg_shared_core_code and cc == pg_shared_core_code and pg_sections and sec in pg_sections)
                    
                    # 1-slot lectures
                    key1 = (sec, cc, d, t)
                    if key1 in x1:
                        if is_pg_core:
                            if not seen_pg_core_lecture:
                                terms.append(x1[key1])
                                seen_pg_core_lecture = True
                        else:
                            terms.append(x1[key1])
                            
                    # 2-slot blocks that COVER slot t
                    for etype in ("T", "P"):
                        # block starting at t covers slots t, t+1
                        key_start = (sec, cc, etype, d, t)
                        if key_start in x2:
                            if is_pg_core:
                                if f"start_{etype}" not in seen_pg_core_blocks:
                                    terms.append(x2[key_start])
                                    seen_pg_core_blocks.add(f"start_{etype}")
                            else:
                                terms.append(x2[key_start])
                        # block starting at t-1 covers slots t-1, t
                        if t > 0:
                            key_prev = (sec, cc, etype, d, t - 1)
                            if key_prev in x2:
                                if is_pg_core:
                                    if f"prev_{etype}" not in seen_pg_core_blocks:
                                        terms.append(x2[key_prev])
                                        seen_pg_core_blocks.add(f"prev_{etype}")
                                else:
                                    terms.append(x2[key_prev])
                
                # Use optimized lookup for dynamic co-faculty blocks covering slot t
                terms.extend(cofac_lookup[(fac, d, t)])

                if len(terms) > 1:
                    model.Add(sum(terms) <= 1)


# ===================================================================
# H1.5 — Dynamic Co-Faculty Logic & Workload Caps
# ===================================================================
def add_co_faculty_logic(model, x2, co_fac, faculty_assignments):
    """
    For every practical (P) block, exactly 2 Co-Faculty members must be assigned.
    The primary faculty assigned to this lab CANNOT be chosen as a co-faculty.
    """
    from collections import defaultdict
    block_to_cofacs = defaultdict(list)
    for (fac_name, sec, cc, d, t), var in co_fac.items():
        block_to_cofacs[(sec, cc, d, t)].append((fac_name, var))

    for (sec, cc, d, t), cofac_list in block_to_cofacs.items():
        k_prac = (sec, cc, "P", d, t)
        if k_prac in x2:
            prac_var = x2[k_prac]
            
            # 1. Exactly 2 co-faculty if the block is scheduled, 0 otherwise.
            all_vars = [var for _, var in cofac_list]
            model.Add(sum(all_vars) == 2 * prac_var)
            
            # 2. Primary faculty cannot be co-faculty
            # Find the primary faculty for this (sec, cc)
            for fac_name, assignments in faculty_assignments.items():
                if (sec, cc) in assignments:
                    # This faculty is the primary teacher! Force their co-fac var to 0.
                    for cf_name, var in cofac_list:
                        if cf_name == fac_name:
                            model.Add(var == 0)

def add_max_workload(model, x1, x2, co_fac, faculty_assignments, faculty_designations,
                     semester="odd", count_cofac_in_workload=True):
    """
    Enforces Maximum Workload Units per faculty.
    1 L, T, or P block = 2 units.
    Limits (Odd): Prof=18, Assoc=24, Assist=28
    Limits (Even): Prof=14, Assoc=18, Assist=24

    count_cofac_in_workload (TEMPORARY FIX flag):
        True  → original behaviour: co-faculty slots count toward the cap.
        False → co-faculty slots are excluded from the cap so they don't
                crowd out primary teaching assignments.
        Revert: set EXCLUDE_COFAC_FROM_WORKLOAD_CAP = False in solver.py.
    """
    from collections import defaultdict
    if semester.lower() == "odd":
        limits = {"Professor": 18, "Associate": 24, "Assistant": 28}
    else:
        limits = {"Professor": 14, "Associate": 18, "Assistant": 24}

    # Pre-calculate co-faculty lookup by faculty name
    cofac_by_fac = defaultdict(list)
    if count_cofac_in_workload:
        for (fac_name, sec, cc, d, t), var in co_fac.items():
            cofac_by_fac[fac_name].append(var)

    for fac, assignments in faculty_assignments.items():
        desig = faculty_designations.get(fac, "Assistant")
        max_units = limits.get(desig, 28)
        
        events = []
        # Primary lectures (1 slot = 2 units)
        for sec, cc in assignments:
            for d in range(5):
                for t in range(7):
                    k1 = (sec, cc, d, t)
                    if k1 in x1:
                        events.append(x1[k1])
                        
                    # Primary blocks (2 slots = 2 units)
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, d, t)
                        if k2 in x2:
                            events.append(x2[k2])
                            
        # Co-faculty blocks — only count toward cap if flag is True
        events.extend(cofac_by_fac[fac])
                
        # Total units = sum(events) * 2
        # sum(events) * 2 <= max_units  =>  sum(events) <= max_units // 2
        model.Add(sum(events) <= max_units // 2)

    # --- GLOBAL WORKLOAD CUT ---
    # Sum of all teaching events across all faculty must be <= sum of all capacities.
    # This redundant constraint helps the solver's lower-bound proving logic.
    all_faculty_events = []
    total_capacity_units = 0
    for fac, assignments in faculty_assignments.items():
        desig = faculty_designations.get(fac, "Assistant")
        total_capacity_units += limits.get(desig, 28) // 2
        
        # Primary events
        for sec, cc in assignments:
            for d in range(5):
                for t in range(7):
                    k1 = (sec, cc, d, t)
                    if k1 in x1: all_faculty_events.append(x1[k1])
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, d, t)
                        if k2 in x2: all_faculty_events.append(x2[k2])
        # Co-faculty events
        if count_cofac_in_workload:
            all_faculty_events.extend(cofac_by_fac[fac])

    if all_faculty_events:
        model.Add(sum(all_faculty_events) <= total_capacity_units)


# ===================================================================
# H2 — No section double-booking
# ===================================================================
def add_no_section_clash(model, x1, x2, section_courses):
    """
    For each section, for each (day, slot): at most 1 course occupies the slot.

    section_courses: dict  section -> list of course_codes
    """
    for sec, courses in section_courses.items():
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                terms = []
                for cc in courses:
                    key1 = (sec, cc, d, t)
                    if key1 in x1:
                        terms.append(x1[key1])
                    for etype in ("T", "P"):
                        key_start = (sec, cc, etype, d, t)
                        if key_start in x2:
                            terms.append(x2[key_start])
                        if t > 0:
                            key_prev = (sec, cc, etype, d, t - 1)
                            if key_prev in x2:
                                terms.append(x2[key_prev])
                if len(terms) > 1:
                    model.Add(sum(terms) <= 1)


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
def add_no_student_gaps(model, x1, x2, section_courses):
    """
    Ensures that if a section has a class at slot t, they MUST have a class at slot t-1.
    This forces all classes to be packed at the start of the day (S1 onwards),
    preventing any gaps in the student's schedule, while allowing them to finish early
    if they run out of weekly hours.
    """
    for sec, courses in section_courses.items():
        for d in range(NUM_DAYS):
            # Create a boolean variable for each slot indicating if it's active
            active = []
            for t in range(NUM_SLOTS):
                terms = []
                for cc in courses:
                    # 1-slot lecture
                    k1 = (sec, cc, d, t)
                    if k1 in x1: terms.append(x1[k1])
                    
                    # 2-slot blocks (covers t if it starts at t OR t-1)
                    for etype in ("T", "P"):
                        k_start = (sec, cc, etype, d, t)
                        if k_start in x2: terms.append(x2[k_start])
                        if t > 0:
                            k_prev = (sec, cc, etype, d, t - 1)
                            if k_prev in x2: terms.append(x2[k_prev])
                            
                # is_active = 1 if any term is 1, else 0
                is_active = model.NewBoolVar(f"active_{sec}_d{d}_t{t}")
                if terms:
                    model.AddMaxEquality(is_active, terms)
                else:
                    model.Add(is_active == 0)
                active.append(is_active)
                
            # Constraint: if slot t is empty, slot t+1 MUST be empty.
            # Equivalently: if slot t+1 is active, slot t MUST be active.
            for t in range(NUM_SLOTS - 1):
                model.AddImplication(active[t+1], active[t])


# ===================================================================
# H4.5 — Morning-first: ALL morning slots (S1-S4) must be filled every day
# ===================================================================
def add_morning_first(model, x1, x2, section_courses):
    """
    Hard constraint: For every section on every day (Mon-Fri), each of the
    4 morning slots (S1-S4, indices 0-3) MUST have a class. This is
    unconditional — morning is always fully occupied. Afternoon slots are
    only used for overflow once all 20 morning slots/week are taken.
    """
    for sec, courses in section_courses.items():
        # PG sections have fewer total course-hours than UG sections and
        # cannot reliably fill all 4 morning slots every day — exempt them.
        is_pg = "PG" in sec or "SP" in sec
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d == 4:
                continue # EXEMPT 7th sem from Friday classes
            if is_pg:
                continue  # EXEMPT PG sections from mandatory morning fill
            for t in MORNING_SLOTS:  # [0, 1, 2, 3]
                # Collect every variable that makes this slot occupied
                terms = []
                for cc in courses:
                    # 1-slot lecture
                    k1 = (sec, cc, d, t)
                    if k1 in x1:
                        terms.append(x1[k1])
                    # 2-slot block starting at t (covers t and t+1)
                    for etype in ("T", "P"):
                        k_start = (sec, cc, etype, d, t)
                        if k_start in x2:
                            terms.append(x2[k_start])
                        # 2-slot block starting at t-1 (covers t-1 and t)
                        if t > 0:
                            k_prev = (sec, cc, etype, d, t - 1)
                            if k_prev in x2:
                                terms.append(x2[k_prev])

                # At least one course must occupy this morning slot
                # Skip sections that don't have enough vars (e.g. PG sections with fewer courses)
                if terms:
                    model.Add(sum(terms) >= 1)


# ===================================================================
# H4.6 — No empty days (every day must have at least one class)
# ===================================================================
def add_no_empty_days(model, x1, x2, section_courses):
    """
    For every section, every day (Monday–Friday) must have at least one
    teaching event (lecture, tutorial, or practical). No day can be blank.
    """
    for sec, courses in section_courses.items():
        for d in range(NUM_DAYS):
            if sec.startswith("7") and d == 4:
                continue # EXEMPT 7th sem from Friday classes
            day_terms = []
            for cc in courses:
                for t in range(NUM_SLOTS):
                    k1 = (sec, cc, d, t)
                    if k1 in x1:
                        day_terms.append(x1[k1])
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, d, t)
                        if k2 in x2:
                            day_terms.append(x2[k2])
            if day_terms:
                model.Add(sum(day_terms) >= 1)


# ===================================================================
# H5 — Faculty gap after teaching
# ===================================================================
def add_faculty_break(model, x1, x2, faculty_assignments, co_fac=None):
    """
    After any primary teaching event, faculty must have ≥1 free slot before
    their next primary event.

    Rules enforced:
      1. Primary lecture at t → no primary event starts at t+1.
      2. Primary block ending at t (started t-1) → no primary event starts at t+1.

    Note: the co-fac ↔ primary break is handled separately by add_co_faculty_break.
    """
    for fac, assignments in faculty_assignments.items():
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                # Collect lecture vars at slot t for this faculty
                lec_terms = []
                for sec, cc in assignments:
                    key1 = (sec, cc, d, t)
                    if key1 in x1:
                        lec_terms.append(x1[key1])

                # Collect primary block vars that END at slot t (started at t-1)
                block_end_terms = []
                if t > 0:
                    for sec, cc in assignments:
                        for etype in ("T", "P"):
                            key_prev = (sec, cc, etype, d, t - 1)
                            if key_prev in x2:
                                block_end_terms.append(x2[key_prev])

                # All primary terms that could START at slot t+1
                if t + 1 < NUM_SLOTS:
                    next_start_terms = []
                    for sec, cc in assignments:
                        key_next = (sec, cc, d, t + 1)
                        if key_next in x1:
                            next_start_terms.append(x1[key_next])
                        for etype in ("T", "P"):
                            key_next_block = (sec, cc, etype, d, t + 1)
                            if key_next_block in x2:
                                next_start_terms.append(x2[key_next_block])

                    # Rule 1: After a primary lecture at t → nothing primary starts at t+1
                    for lv in lec_terms:
                        for nv in next_start_terms:
                            model.Add(lv + nv <= 1)

                    # Rule 2: After a primary block ending at t → nothing primary starts at t+1
                    for bv in block_end_terms:
                        for nv in next_start_terms:
                            model.Add(bv + nv <= 1)


# ===================================================================
# H5.5 — Co-faculty adjacency break (TEMPORARY FIX)
# ===================================================================
def add_co_faculty_break(model, x1, x2, co_fac, faculty_assignments):
    """
    TEMPORARY FIX: Ensures a 1-slot gap between a faculty's primary teaching
    events and any co-faculty practical blocks they are assigned to.

    Rules:
      A) After a primary event ends at slot t → co-fac block cannot START at t+1.
      B) After a co-fac block ends at t+1 (started at t) → no primary event
         can START at t+2.

    To revert: set ENABLE_CO_FACULTY_BREAK = False in solver.py.
    """
    from collections import defaultdict

    # Build fast lookup: (fac, d) -> list of (t_start, var)
    fac_cofac_by_day = defaultdict(list)
    for (fac_name, sec, cc, d, t_start), var in co_fac.items():
        fac_cofac_by_day[(fac_name, d)].append((t_start, var))

    for fac, assignments in faculty_assignments.items():
        for d in range(NUM_DAYS):
            cofac_today = fac_cofac_by_day.get((fac, d), [])
            if not cofac_today:
                continue

            for t in range(NUM_SLOTS):
                # --- Primary events that END at slot t ---
                primary_ending_at_t = []
                for sec, cc in assignments:
                    k1 = (sec, cc, d, t)
                    if k1 in x1:
                        primary_ending_at_t.append(x1[k1])  # lecture at t
                    if t > 0:
                        for etype in ("T", "P"):
                            k_prev = (sec, cc, etype, d, t - 1)
                            if k_prev in x2:
                                primary_ending_at_t.append(x2[k_prev])  # block t-1,t

                # Rule A: primary ends at t → no co-fac block starts at t+1
                if t + 1 < NUM_SLOTS and primary_ending_at_t:
                    cofac_next = [v for (ts, v) in cofac_today if ts == t + 1]
                    for pv in primary_ending_at_t:
                        for cv in cofac_next:
                            model.Add(pv + cv <= 1)

                # Rule B: co-fac starts at t (ends at t+1) → no primary starts at t+2
                cofac_at_t = [v for (ts, v) in cofac_today if ts == t]
                if t + 2 < NUM_SLOTS and cofac_at_t:
                    primary_starting_t2 = []
                    for sec, cc in assignments:
                        k1 = (sec, cc, d, t + 2)
                        if k1 in x1:
                            primary_starting_t2.append(x1[k1])
                        for etype in ("T", "P"):
                            k2 = (sec, cc, etype, d, t + 2)
                            if k2 in x2:
                                primary_starting_t2.append(x2[k2])
                    for cv in cofac_at_t:
                        for pv in primary_starting_t2:
                            model.Add(cv + pv <= 1)

                    # Rule C: co-fac starts at t (ends at t+1) → no OTHER co-fac
                    #         block starts at t+2 (1-slot break between co-fac duties)
                    cofac_at_t2 = [v for (ts, v) in cofac_today if ts == t + 2]
                    for cv1 in cofac_at_t:
                        for cv2 in cofac_at_t2:
                            model.Add(cv1 + cv2 <= 1)


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
def add_spread_penalty(model, x1, x2, section_courses):
    """
    Penalize having >1 lecture/block of the same subject on the same day.
    Returns a list of penalty BoolVars (each worth 1 penalty point).
    """
    penalties = []
    for sec, courses in section_courses.items():
        for cc in courses:
            for d in range(NUM_DAYS):
                day_vars = []
                for t in range(NUM_SLOTS):
                    if (sec, cc, d, t) in x1:
                        day_vars.append(x1[(sec, cc, d, t)])
                for t in VALID_BLOCK_STARTS:
                    for etype in ("T", "P"):
                        if (sec, cc, etype, d, t) in x2:
                            day_vars.append(x2[(sec, cc, etype, d, t)])
                if len(day_vars) >= 2:
                    # Penalize if more than 1 event of this course on this day
                    total = sum(day_vars)
                    penalty = model.NewBoolVar(f"spread_{sec}_{cc}_d{d}")
                    model.Add(total >= 2).OnlyEnforceIf(penalty)
                    model.Add(total <= 1).OnlyEnforceIf(penalty.Not())
                    penalties.append(penalty)
        
        # The pigeonhole cut for spread penalties was removed here 
        # because it could cut off valid solutions where a single day
        # absorbs multiple events (e.g. 3 events on 1 day).

    return penalties


# ===================================================================
# S2 — No subject repeated in S1 (first slot) across the week (soft)
# ===================================================================
def add_first_slot_repeat_penalty(model, x1, x2, section_courses):
    """
    Strongly penalize any subject that occupies slot S1 (t=0, the first
    slot of the day) on more than one day in the week.

    For each (section, course), we collect the S1 variables across all days:
      - x1[(sec, cc, d, 0)]            — lecture in S1
      - x2[(sec, cc, "T"/"P", d, 0)]   — block starting at S1 (covers S1+S2)

    If the sum of those across all days > 1, we apply a penalty weighted
    at 20 points — much heavier than the 1-point spread penalty — so the
    solver strongly prefers putting each subject in S1 at most once.

    Returned as a list of (weight * BoolVar) terms for the objective.
    """
    WEIGHT = 20
    weighted_penalties = []

    for sec, courses in section_courses.items():
        for cc in courses:
            # Collect all S1 vars across the week for this (sec, course)
            s1_vars = []
            for d in range(NUM_DAYS):
                k1 = (sec, cc, d, 0)
                if k1 in x1:
                    s1_vars.append(x1[k1])
                for etype in ("T", "P"):
                    k2 = (sec, cc, etype, d, 0)
                    if k2 in x2:
                        s1_vars.append(x2[k2])

            # Only meaningful if the subject can appear in S1 on ≥2 days
            if len(s1_vars) < 2:
                continue

            # count = number of days this subject occupies S1
            # extra  = max(0, count - 1) = how many times it repeats in S1
            # Penalize each extra occurrence with weight WEIGHT.
            # We model this with auxiliary BoolVars for each pair (d1, d2).
            # Simpler: penalize sum > 1 via a surplus integer variable.
            total_s1 = sum(s1_vars)

            # surplus = max(0, total_s1 - 1)
            # Only the lower bound is needed: since we Minimize(WEIGHT * surplus),
            # the solver will drive surplus down to exactly max(0, total_s1 - 1).
            # The equality form would make the model infeasible when total_s1 = 0
            # (it would require surplus = -1, violating the IntVar floor of 0).
            surplus = model.NewIntVar(0, len(s1_vars) - 1,
                                     f"s1_surplus_{sec}_{cc}")
            model.Add(surplus >= total_s1 - 1)

            weighted_penalties.append(WEIGHT * surplus)

    return weighted_penalties

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

    # Constraint 3: no room double-booking
    # Two events conflict if their occupied slots overlap on the same day+room.
    
    # NEW: Build a lookup for events covering each (day, slot)
    from collections import defaultdict
    slot_to_events = defaultdict(list)
    for i, (sec, cc, etype, d2, t2, duration, active_var) in enumerate(needs_room):
        # Store index i and the room-assignment variable for this event
        slot_to_events[(d2, t2)].append(i)
        if duration == 2:
            slot_to_events[(d2, t2 + 1)].append(i)

    for room in LAB_ROOMS:
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                # Collect all room-vars whose event covers slot t
                covering = []
                event_indices = slot_to_events[(d, t)]
                for idx in event_indices:
                    (sec, cc, etype, d2, t2, duration, active_var) = needs_room[idx]
                    k_room = (sec, cc, etype, d, t2, room)
                    if k_room in lab_room:
                        covering.append(lab_room[k_room])
                            
                if len(covering) > 1:
                    model.Add(sum(covering) <= 1)

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

def add_faculty_morning_penalty(model, x1, x2, faculty_assignments):
    """
    Penalize if a faculty has NO morning sessions (S1-S4) across the entire week.
    Returns a list of penalties to append to the master penalty list.
    """
    penalties = []
    for fac, assignments in faculty_assignments.items():
        morning_vars = []
        for sec, cc in assignments:
            for d in range(NUM_DAYS):
                for t in MORNING_SLOTS:
                    k1 = (sec, cc, d, t)
                    if k1 in x1:
                        morning_vars.append(x1[k1])
                # Blocks starting in the morning
                # 0, 2 are strictly morning blocks (covering S1-S2, S3-S4)
                for t in [0, 2]:
                    for etype in ("T", "P"):
                        k2 = (sec, cc, etype, d, t)
                        if k2 in x2:
                            morning_vars.append(x2[k2])
                            
        if morning_vars:
            # has_morning_fac is 1 if they have ANY morning sessions, 0 if NONE.
            has_morning_fac = model.NewBoolVar(f"has_morning_{fac}")
            model.AddMaxEquality(has_morning_fac, morning_vars)
            
            # Penalize the LACK of morning sessions.
            # Using positive penalties helps the solver's lower-bound proving logic.
            no_morning = model.NewBoolVar(f"no_morning_{fac}")
            model.Add(no_morning == 1 - has_morning_fac)
            
            penalties.append(30 * no_morning)
            
    return penalties
