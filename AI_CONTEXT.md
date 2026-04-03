# AI Context — Timetable Generator

## Project Summary

A constraint-based timetable generator that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints. Built for a Computer Science department managing odd/even semester faculty assignments across multiple semesters (1, 3, 5, 7 / 2, 4, 6, 8).

## Tech Stack

- **Language:** Python 3.11+ (Standard Windows CPython)
- **UI:** Streamlit (pure Python, no frontend code)
- **Database:** MongoDB Atlas + PyMongo
- **Constraint Solver:** `python-constraint` (installed), Google OR-Tools CP-SAT (planned)
- **Exports:** Excel (openpyxl), PDF (reportlab)

## Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & landing page
├── pages/
│   └── 1_Input_Data.py      # Faculty & course data upload via Excel
├── db.py                    # PyMongo connection (cached MongoClient)
├── config.py                # Loads MONGO_URI & DB_NAME from .env
├── requirements.txt         # Python dependencies
├── Template.xlsx            # Blank Excel template for users
├── Test.xlsx                # Sample test data (31 faculty, 24 courses)
├── AI_CONTEXT.md            # This file
├── TECH_STACK.md            # Detailed tech stack rationale
└── README.md                # Project README
```

## Implementation Status

**Currently Built:**
- Project scaffolding (`app.py`, `db.py`, `config.py`, `requirements.txt`).
- **Page 1 (Input Data):** Streamlit UI to upload Excel files with two sheets:
  - **Faculty_Assignments** — Two-row header format with Sr No., Name, Designation, Subject columns (S1/Sem, S2/Sem, S3/Sem), and Lab columns (L1/Sem, L2/Sem).
  - **Courses** — Single-row header with Course Code, Course Name, L, T, P, Lecture in Lab?, Tutorial in Lab?, Semester, Elective.
- Robust column-matching parser using pattern-based header detection (handles partial matches, extra columns).
- MongoDB integration: saves faculty to `faculty_odd` / `faculty_even`, courses to `courses` collection.
- Save & delete buttons with confirmation dialogs for both faculty and course data.
- Semester selector (Odd/Even) for faculty data partitioning.
- Current database state preview at the bottom of the page.

**Pending Features:**
- **Page 2 (Constraints):** UI to define system-wide and teacher-specific constraints.
- **Page 3 (Generate):** The actual constraint solver logic.
- **Page 4 (Export):** Producing the final PDF and Excel timetable matrices.

## Excel File Format

### Faculty_Assignments Sheet (two-row header)

| Sr No. | Name | Designation | Subject |       |       |       |       |       | Lab   |       |       |       |
|--------|------|-------------|---------|-------|-------|-------|-------|-------|-------|-------|-------|-------|
|        |      |             | S1      | Sem S1| S2    | Sem S2| S3    | Sem S3| L1    | Sem L1| L2    | Sem L2|
| 1      | Dr. X | Professor  | PPC     | 1     | DBMS  | 5     |       |       | DBMS LAB | 5 |       |       |

- The parser finds the header row by looking for "Sr No." + "Name" + "Designation" in the same row.
- Subject columns: pairs of (S1, Sem of S1), (S2, Sem of S2), (S3, Sem of S3).
- Lab columns: pairs of (L1, Sem of L1), (L2, Sem of L2).

### Courses Sheet (single-row header)

| Course Code | Course Name | L | T | P | Lecture in Lab? | Tutorial in Lab? | Semester | Elective |
|-------------|-------------|---|---|---|-----------------|------------------|----------|----------|
| 24CS32      | Digital Design... | 3 | 0 | 1 | No | No | 3 | No |

- Parsed via pattern-based column matching (e.g. "course code" matches "code", "course").
- `Elective` column is optional — defaults to "No" if absent.

## MongoDB Collections

| Collection | Contents | Key Fields |
|---|---|---|
| `faculty_odd` | Odd semester faculty records | `sl_no`, `name`, `designation`, `subjects[]`, `labs[]`, `semester` |
| `faculty_even` | Even semester faculty records | Same as above |
| `courses` | All course definitions | `course_code`, `course_name`, `L`, `T`, `P`, `lecture_in_lab`, `tutorial_in_lab`, `semester`, `elective` |

## Key Concepts

### Hard Constraints (must never be violated)
- No teacher assigned to two classes at the same time
- No room double-booked
- Room capacity ≥ class size
- Mandatory subject hours per week fulfilled

### Soft Constraints (optimized via objective function)
- Teacher time-slot preferences
- Minimize gaps between consecutive classes for students
- Spread subjects evenly across the week
- Avoid back-to-back classes for teachers

## Conventions
- Use Python dataclasses for data schemas
- Streamlit multi-page app structure (`pages/` directory)
- MongoDB collections: `faculty_odd`, `faculty_even`, `courses`, `constraints`, `timetables`
- Environment variables stored in `.env` (loaded via `python-dotenv`)
- **Excel parsing:** Uses `data_only=True` via `openpyxl`. Faculty sheet uses two-row header detection; Courses sheet uses pattern-based column matching with fallback defaults for optional columns.
