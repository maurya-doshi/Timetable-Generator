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
VALID_BLOCK_STARTS = [0, 1, 2, 4, 5]  # pairs: (0,1),(1,2),(2,3),(4,5),(5,6)


# ===================================================================
# H1 — No faculty double-booking
# ===================================================================
def add_no_faculty_clash(model, x1, x2, faculty_assignments):
    """
    For each faculty member, for each (day, slot): at most 1 teaching event.

    faculty_assignments: dict  faculty_name -> list of (section, course_code)
    """
    for fac, assignments in faculty_assignments.items():
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                terms = []
                for sec, cc in assignments:
                    # 1-slot lectures
                    key1 = (sec, cc, d, t)
                    if key1 in x1:
                        terms.append(x1[key1])
                    # 2-slot blocks that COVER slot t
                    for etype in ("T", "P"):
                        # block starting at t covers slots t, t+1
                        key_start = (sec, cc, etype, d, t)
                        if key_start in x2:
                            terms.append(x2[key_start])
                        # block starting at t-1 covers slots t-1, t
                        if t > 0:
                            key_prev = (sec, cc, etype, d, t - 1)
                            if key_prev in x2:
                                terms.append(x2[key_prev])
                if len(terms) > 1:
                    model.Add(sum(terms) <= 1)


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
# H4 — Morning slots must be filled
# ===================================================================
def add_morning_filled(model, x1, x2, section_courses):
    """
    For each section, each day, each morning slot (S1-S4): exactly 1 course.
    """
    for sec, courses in section_courses.items():
        for d in range(NUM_DAYS):
            for t in MORNING_SLOTS:
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
                if terms:
                    model.Add(sum(terms) == 1)


# ===================================================================
# H5 — Faculty gap after teaching
# ===================================================================
def add_faculty_break(model, x1, x2, faculty_assignments):
    """
    After any teaching event, faculty must have ≥1 free slot before next event.

    Implementation: for each faculty, day — track which slots have a new event
    starting and ensure no event starts in the slot immediately following the
    end of any event.

    Simpler formulation: for each faculty, day, slot t:
        if faculty is busy at slot t via a LECTURE → slot t+1 must be free
        if faculty is busy at slot t via end of a BLOCK (started at t-1) → slot t+1 free
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

                # Collect block vars that END at slot t (started at t-1)
                block_end_terms = []
                if t > 0:
                    for sec, cc in assignments:
                        for etype in ("T", "P"):
                            key_prev = (sec, cc, etype, d, t - 1)
                            if key_prev in x2:
                                block_end_terms.append(x2[key_prev])

                # All terms that could START at slot t+1
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

                    # After a lecture at t → nothing starts at t+1
                    for lv in lec_terms:
                        for nv in next_start_terms:
                            model.Add(lv + nv <= 1)

                    # After a block ending at t → nothing starts at t+1
                    for bv in block_end_terms:
                        for nv in next_start_terms:
                            model.Add(bv + nv <= 1)


# ===================================================================
# H6 — OE concurrency (all OE on Monday S5)
# ===================================================================
def add_oe_concurrency(model, x1, section_courses, oe_course_codes):
    """
    All open-elective courses are fixed to Monday (day 0), slot S5 (slot 4).
    They must be scheduled as a 1-slot lecture at that exact position.
    """
    DAY_MON = 0
    SLOT_S5 = 4
    for sec, courses in section_courses.items():
        for cc in courses:
            if cc not in oe_course_codes:
                continue
            # Force the lecture at Monday S5
            key = (sec, cc, DAY_MON, SLOT_S5)
            if key in x1:
                model.Add(x1[key] == 1)
            # Forbid this course on every other (day, slot)
            for d in range(NUM_DAYS):
                for t in range(NUM_SLOTS):
                    if (d, t) == (DAY_MON, SLOT_S5):
                        continue
                    k = (sec, cc, d, t)
                    if k in x1:
                        model.Add(x1[k] == 0)


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
                  shared_core_code, shared_pe_codes):
    """
    Shared PG core / PE courses must happen at the same (day, slot)
    for both PG sections (SP1, SP2).
    """
    if len(pg_sections) < 2:
        return
    shared_codes = []
    if shared_core_code:
        shared_codes.append(shared_core_code)
    shared_codes.extend(shared_pe_codes or [])

    s1, s2 = pg_sections[0], pg_sections[1]
    for cc in shared_codes:
        if cc not in section_courses.get(s1, []) or cc not in section_courses.get(s2, []):
            continue
        # Tie lecture vars
        for d in range(NUM_DAYS):
            for t in range(NUM_SLOTS):
                k1 = (s1, cc, d, t)
                k2 = (s2, cc, d, t)
                if k1 in x1 and k2 in x1:
                    model.Add(x1[k1] == x1[k2])
            # Tie block vars
            for t in VALID_BLOCK_STARTS:
                for etype in ("T", "P"):
                    k1 = (s1, cc, etype, d, t)
                    k2 = (s2, cc, etype, d, t)
                    if k1 in x2 and k2 in x2:
                        model.Add(x2[k1] == x2[k2])


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
    "Thursday": 3, "Friday": 4,
}


def add_maths_locks(model, x1, maths_slots, maths_course_code="MATHS"):
    """
    Lock pre-assigned maths slots.
    maths_slots: list of {"Class": "3A", "Day": "Monday", "Slot": "S1 (...)", ...}
    """
    for entry in maths_slots:
        sec = entry.get("Class", "")
        day_label = entry.get("Day", "")
        slot_label = entry.get("Slot", "")
        if not sec or not day_label or not slot_label:
            continue
        d = DAY_LABEL_TO_IDX.get(day_label)
        t = SLOT_LABEL_TO_IDX.get(slot_label)
        if d is None or t is None:
            continue
        key = (sec, maths_course_code, d, t)
        if key in x1:
            model.Add(x1[key] == 1)


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
    return penalties
