# AI Context — Timetable Generator

## Project Summary

A constraint-based timetable generator that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints.

## Tech Stack

- **Language:** Python 3.11+ (Standard Windows CPython)
- **UI:** Streamlit (pure Python, no frontend code)
- **Database:** MongoDB Atlas + PyMongo
- **Constraint Solver:** Google OR-Tools (CP-SAT)
- **Exports:** PDF (ReportLab), Excel (openpyxl)

## Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & landing page
├── pages/
│   ├── 0_Settings.py        # Settings configuration (default time limit, section mapping)
│   ├── 1_Input_Data.py      # Dual-sheet single-file parser
│   ├── 2_Constraints.py     # Hard constraints & special mappings builder
│   └── 3_Generate.py        # Run solver, view results, load past runs, & export PDF/Excel
├── engine/
│   ├── solver.py            # OR-Tools CP-SAT model builder (Institutional OE cleanup included)
│   ├── constraints.py       # Constraint definitions (H1-H10, S1-S2)
│   ├── pdf_export.py        # ReportLab PDF generation
│   ├── excel_export.py      # openpyxl Excel generation
│   └── preflight.py         # Validates data and assignments before running the solver
├── db.py                    # PyMongo connection & helpers (result saving/loading)
├── config.py                # App settings & DB URI
└── requirements.txt         # Python dependencies
```

## Implementation Status

**Fully Implemented:**
- **Page 0 (Settings):** Allows configuration of default solver time limits, parallel workers, and dynamic section mapping per semester.
- **Page 1 (Input Data):** Streamlit UI to upload a single Excel file containing two mandatory sheets: `Faculty_Assignments` and `Courses`. Custom robust Excel parsing logic. MongoDB integration saving to `faculty_odd`/`faculty_even` and `courses` collections.
- **Page 2 (Constraints):** Interactive builder for Open Electives, AEC, Shared PG Classes, Manual Maths Overrides grid, and CSE Lab Allocations for 1st/2nd semester.
- **Page 3 (Generate):** Full CP-SAT solver with pre-flight checks, solver settings, section & faculty timetable display, diffs against previous runs, and PDF/Excel export. Connects to MongoDB to save/load/delete generated timetable results.
- **Engine:** 
  - 10 hard constraints (faculty clash, section clash, weekly hours, no gaps, morning-first, empty days, faculty break, OE/AEC concurrency, PG shared, maths/lab locks) and 3 soft constraints (spread, first-slot repeat, morning penalty). 
  - Dynamic co-faculty assignment for practicals with workload caps.
  - Exempts 1st sem (used only for faculty blocking), PG, and 7th sem from certain rigid time constraints.
  - Institutional OE cleanup: Auto-removes dummy CSE faculty assignments when they are listed for all sections, allowing the OE block to schedule without faculty clash.

## Conventions
- Use Python dicts directly for fast PyMongo integration.
- Streamlit multi-page app structure (`pages/` directory).
- MongoDB collections: `faculty_odd`, `faculty_even`, `courses`, `constraints`, `settings`, `timetable_results`.
- **Excel parsing:** Expects a single uploaded `.xlsx` file with `Faculty_Assignments` and `Courses` sheets.
