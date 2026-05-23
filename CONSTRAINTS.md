# Timetable Generator — Constraint List

This document lists every constraint applied by the CP-SAT solver.
Constraints are divided into **Hard** (must always be satisfied) and **Soft** (minimized in objective).

---

## Hard Constraints

Hard constraints are absolute rules. The solver will never produce a timetable that violates them.
If they cannot all be satisfied simultaneously, the solver returns INFEASIBLE.

### H1 — No Faculty Double-Booking
A faculty member can teach at most one class in any given (day, slot).
Covers lectures (x1), 2-slot blocks (x2), and dynamic co-faculty assignments.
PG Shared Core lectures are deduplicated (SP-1 and SP-2 sit together, so the faculty is counted only once).

### H1.5 — Co-Faculty Slot Logic
When a co-faculty is dynamically assigned to cover a tutorial or practical block,
that co-faculty variable is linked to the block's scheduled (day, slot).
This ensures the assignment is only active when the block is actually scheduled.

### H2 — No Section Double-Booking
A section can attend at most one class in any given (day, slot).
A 2-slot block (tutorial/practical) occupies both slot t and t+1.

### H3 — Weekly Hours Met
Each course must be scheduled for exactly its required number of hours per week.
  - L hours → L individual 1-slot lecture events
  - T hours → one 2-slot tutorial block (T>0 means exactly 1 tutorial block, covering 2 slots)
  - P hours → one 2-slot practical block (P>0 means exactly 1 practical block, covering 2 slots)
    Exception: courses with P=4 get two 2-slot practical blocks.

### H4 — No Student Gaps (Compressed Schedule)
Students must not have a free slot sandwiched between two occupied slots on the same day.
Formally: if slot t and slot t+2 are both occupied, then slot t+1 must also be occupied.
The lunch break (between S4 and S5) is treated as a natural gap and is exempt.

### H5 — Morning Slots Filled First
All morning slots (S1–S4) for a section on a given day must be scheduled before any afternoon slot (S5–S7) is used.
This prevents isolated afternoon-only days.

### H5.5 — No Empty Teaching Days
If a section has any events in a week, at least one of those events must fall in S1–S4 on each day that has events.
Prevents a section from having an afternoon-only day with no morning classes.

### H6 — Faculty Break Between Consecutive Classes
A faculty member must have at least one free slot between any two of their teaching events on the same day.
Specifically: if a faculty teaches (or a block ends) at slot t, their next event cannot start at slot t+1.
This ensures faculty are never scheduled back-to-back without any gap.

### H6.5 — Co-Faculty Break Between Classes
The same back-to-back gap rule applies when a faculty is acting as a co-faculty for a practical or tutorial block.
If a faculty's primary event ends at slot t, a co-faculty block cannot start at t+1.
If a faculty's co-faculty block ends at t+1, no primary event can start at t+2.
This also prevents two co-faculty assignments from being placed back-to-back without a gap.

### H7 — OE (Open Elective) Concurrency
All sections that share an Open Elective must be scheduled for that elective at the same (day, slot).
This allows students from different sections to attend together.

### H8 — AEC (Ability Enhancement Course) Concurrency
All 3rd-semester sections must have their AEC course at the same (day, slot).
All 4th-semester sections must have their AEC course at the same (day, slot).

### H9 — PG Shared Core Lecture
The two PG specialization groups (SP-1 and SP-2) must attend the shared core course
at the same (day, slot) so they can sit in the same classroom.

### H10 — Maths Slot Locks
Mathematics lectures and tutorials for 1st and 2nd semester sections are locked
to specific (day, slot) positions as defined in the uploaded Maths data.

### H11 — CSE Lab Locks
Certain (day, slot, room) combinations are pre-blocked for 1st and 2nd semester
CSE practicals, preventing higher semesters from booking those rooms at those times.

### H12 — Subject Spread Across Days
For any (section, course), at most 1 event (lecture or block) may be scheduled on a single day.
This guarantees no subject's lectures are "bunched" on the same day.
Since no course has more than 4 total events and there are 5 days, this is always satisfiable.

### H13 — No S1 (9:00 AM) Repeat
For any (section, course), the course may occupy the first slot of the day (S1, 9:00–9:55 AM)
on at most one day per week.
This prevents students from starting every morning with the same subject.

### H14 — Lab Room Assignment
Each scheduled practical or tutorial block must be assigned to exactly one of the four available
CSE labs (CSE Lab 1–4). No two events may share the same lab room at the same time.
A 2-slot block occupies the room for both slot t and slot t+1.

### H15 — Friday Half-Day
No section may be scheduled in slots S5, S6, or S7 (afternoon) on Fridays.
Friday is treated as a half-day (morning only).

### H16 — Faculty Workload Cap
Faculty workload is capped based on designation:
  - Professor: max 16 slots/week
  - Associate Professor: max 16 slots/week
  - Assistant Professor: max 20 slots/week
  - (default): max 20 slots/week
Co-faculty hours are counted toward the cap.

---

## Soft Constraints

Soft constraints are preferences that the solver tries to satisfy but may occasionally violate
if doing so is the only way to satisfy all hard constraints. They are expressed as penalty
terms in the objective function; the solver minimizes the total penalty.

### S1 — Co-Faculty Prefers to Teach Their Own Courses (Weight: 100 per mismatch slot)
When a co-faculty is dynamically assigned to cover a tutorial or practical block,
the solver prefers to assign a faculty member who already teaches that course to another section.
If a faculty is assigned as co-faculty for a course they do not normally teach, each such
slot incurs a penalty of 100, strongly discouraging mismatched assignments.

---

## Notes

- The solver is Google OR-Tools CP-SAT.
- 8 parallel workers are used.
- Time limit is configurable in the UI (default shown on the Generate page).
- The solver declares a solution OPTIMAL when the gap between the best found solution
  and the proven lower bound is within 5% (relative) or 30 penalty points (absolute).
