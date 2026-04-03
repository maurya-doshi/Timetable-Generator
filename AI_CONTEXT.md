# AI Context — Timetable Generator

## Project Summary

A constraint-based timetable generator that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints.

## Tech Stack

- **Language:** Python 3.11+ (Standard Windows CPython)
- **UI:** Streamlit (pure Python, no frontend code)
- **Database:** MongoDB Atlas + PyMongo
- **Constraint Solver:** `python-constraint`
- **Exports:** Excel (openpyxl), PDF (reportlab)

## Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & UI
├── pages/
│   ├── 1_Input_Data.py      # Teachers, subjects, rooms, sections
│   ├── 2_Constraints.py     # Define hard & soft constraints
│   ├── 3_Generate.py        # Run solver & view results
│   └── 4_Export.py           # Download as PDF / Excel
├── engine/
│   ├── solver.py            # OR-Tools CP-SAT model builder
│   └── constraints.py       # Constraint definitions
├── db.py                    # PyMongo connection & helpers
├── models.py                # Data schemas (dataclasses)
├── config.py                # App settings & DB URI
└── requirements.txt
```

## Implementation Status

**Currently Built:**
- Project scaffolding (`app.py`, `db.py`, `config.py`, `requirements.txt`).
- **Page 1 (Input Data):** Streamlit UI to upload Excel files for faculty/subject assignments.
- Custom Excel parsing logic to handle merged headers, automatically locating "Subject" and "Lab" columns scanning up to row 100 and skipping Sl. No/Name columns.
- MongoDB integration saving uploaded faculty to `faculty_odd` and `faculty_even` collections.

**Pending Features:**
- **Page 2 (Constraints):** UI to define system-wide and teacher-specific constraints.
- **Page 3 (Generate):** The actual CP-SAT / `python-constraint` solver logic.
- **Page 4 (Export):** Producing the final PDF and Excel timetable matrices.

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

### How `python-constraint` Works Here
- Instantiated as `Problem()` instance.
- Decision variables and their domains are defined.
- Hard constraints are added as solver constraints.
- A manual optimization function or heuristic may be needed for soft constraints since `python-constraint` is a pure CSP solver.

## Conventions
- Use Python dataclasses for data schemas
- Streamlit multi-page app structure (`pages/` directory)
- MongoDB collections: `faculty_odd`, `faculty_even`, `subjects`, `rooms`, `sections`, `constraints`, `timetables`
- Environment variables stored in `.env` (loaded via `python-dotenv`)
- **Excel parsing:** Uses `data_only=True` via `openpyxl` to handle custom merged headers in the faculty allotment files. Scan dynamic ranges to locate 'Subject' and 'Lab' string-based fields.
