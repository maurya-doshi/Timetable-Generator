# AI Context — Timetable Generator

## Project Summary

A constraint-based timetable generator that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints.

## Tech Stack

- **Language:** Python 3.11+ (Standard Windows CPython)
- **UI:** Streamlit (pure Python, no frontend code)
- **Database:** MongoDB Atlas + PyMongo
- **Constraint Solver:** Google OR-Tools (CP-SAT)
- **Exports:** Excel (openpyxl)

## Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & UI
├── pages/
│   ├── 1_Input_Data.py      # Dual-sheet single-file parser
│   ├── 2_Constraints.py     # Hard constraints & special mappings builder
│   ├── 3_Generate.py        # Run solver & view results
│   └── 4_Export.py           # Download as PDF / Excel
├── engine/
│   ├── solver.py            # OR-Tools CP-SAT model builder
│   └── constraints.py       # Constraint definitions
├── db.py                    # PyMongo connection & helpers
```

## Implementation Status

**Currently Built:**
- Project scaffolding (`app.py`, `db.py`, `config.py`, `requirements.txt`).
- **Page 1 (Input Data):** Streamlit UI to upload a **single** Excel file containing **two** mandatory sheets: `Faculty_Assignments` and `Courses`.
- Custom robust Excel parsing logic handling these two sheets simultaneously, reading Faculty mappings (via a two-row header setup) and Course details (L/T/P, labs).
- MongoDB integration saving Faculty to `faculty_odd` and `faculty_even` collections and Subject info to the `courses` collection.
- **Page 2 (Constraints):** Interactive Builder mapped directly to the `courses` collection fields (`course_code`/`course_name`), letting users configure Open Electives, AEC, Shared PG Classes, and Manual Maths Overrides grid.

**Pending Features:**
- **Page 3 (Generate):** The actual CP-SAT OR-Tools solver logic processing boolean decision variables across our 5x7 matrix structure.
- **Page 4 (Export):** Producing the final Timetables.

## Conventions
- Use Python dicts directly for fast PyMongo integration.
- Streamlit multi-page app structure (`pages/` directory).
- MongoDB collections: `faculty_odd`, `faculty_even`, `courses`, `constraints`, `timetables`.
- **Excel parsing:** Expects a single uploaded `.xlsx` file mapping straight to the aforementioned schemas without relying heavily on dynamic scanning anymore to avoid ambiguity.
