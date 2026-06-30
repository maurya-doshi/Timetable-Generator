---
title: Timetable Generator
emoji: 📅
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: "1.30.0"
python_version: "3.10"
app_file: app.py
pinned: false
---

# 📅 Timetable Generator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Built%20with-Streamlit-FF4B4B.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A constraint-based timetable generator for academic institutions. Upload faculty & course data, configure scheduling rules, and let the **Google OR-Tools CP-SAT** solver build a clash-free timetable automatically. Export section and faculty timetables to PDF in one click.

---

## ✨ Features

- **CP-SAT Constraint Solver:** 10 hard constraints (no double-booking, mandatory weekly hours, lab contiguity, maths/lab slot locks, OE/AEC concurrency, PG shared classes, etc.) + 3 soft constraints (subject spread, morning-first preference, first-slot penalty).
- **3-Page Streamlit App:**
  - **Input Data** — Upload a single `.xlsx` file containing two sheets (`Faculty_Assignments` & `Courses`) for Odd or Even semester.
  - **Constraints** — Interactively configure Open Electives, AEC subjects, shared PG classes, manual maths slot overrides, and CSE lab allocations.
  - **Generate** — Run the solver with a configurable time limit, view per-section and per-faculty timetables, and export a formatted PDF.
- **Smart Excel Parser:** Handles the two-row header format for faculty sheets and round-robin section distribution when semester columns specify a whole semester rather than individual sections.
- **PDF Export:** Clean, formatted timetables for every section and faculty member via ReportLab.
- **Cloud Persistence:** MongoDB Atlas (PyMongo) stores faculty, courses, constraints, and generated timetables.

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| **UI** | [Streamlit](https://streamlit.io/) |
| **Solver** | [Google OR-Tools](https://developers.google.com/optimization/cp/cp_solver) (CP-SAT) |
| **Database** | [MongoDB Atlas](https://www.mongodb.com/) + PyMongo |
| **PDF Export** | [ReportLab](https://pypi.org/project/reportlab/) |
| **Excel Parsing** | [OpenPyXL](https://openpyxl.readthedocs.io/) |
| **Data** | [Pandas](https://pandas.pydata.org/) |

## 📁 Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & landing page
├── pages/
│   ├── 1_Input_Data.py      # Dual-sheet Excel parser & MongoDB uploader
│   ├── 2_Constraints.py     # OE, AEC, PG, maths & lab allocation builder
│   └── 3_Generate.py        # Run solver, view results & export PDF
├── engine/
│   ├── solver.py            # OR-Tools CP-SAT model builder
│   ├── constraints.py       # Constraint definitions (H1–H10, S1–S3)
│   └── pdf_export.py        # ReportLab PDF generation
├── db.py                    # PyMongo connection & helpers
├── config.py                # App settings & DB URI
└── requirements.txt         # Python dependencies
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- A MongoDB cluster (local or [Atlas free tier](https://www.mongodb.com/atlas))

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/maurya-doshi/Timetable-Generator.git
   cd Timetable-Generator
   ```

2. **Set up a virtual environment (recommended):**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS / Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Create a `.env` file in the root directory:
   ```env
   MONGO_URI=mongodb+srv://<username>:<password>@cluster0.example.mongodb.net/
   DB_NAME=timetable_generator
   ```

### Running the App

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser, then follow the three-step workflow:

1. **Input Data** — Upload your `.xlsx` file (must contain `Faculty_Assignments` and `Courses` sheets) and save to the database.
2. **Constraints** — Tag Open Elective, AEC, and PG subjects; set maths slot locks and lab allocations.
3. **Generate** — Set a solver time limit, click **Generate Timetable**, and download the PDF.

### Excel File Format

The app expects a **single `.xlsx` file** with two sheets:

**`Faculty_Assignments`** — Two-row header format:

| Sr No. | Name | Designation | Subject | | Lab | |
|--------|------|-------------|---------|---|-----|---|
| | | | S1 | Sem S1 | L1 | Sem L1 |
| 1 | Dr. Smith | Assistant Prof | CS301 | 3A | CS391 | 3A |

> In `Sem` columns, enter a specific section (`3A`) or a whole semester number (`3`) to auto-distribute across all sections round-robin.

**`Courses`** — Single-row header format:

| Course Code | Course Name | L | T | P | Lecture in Lab? | Tutorial in Lab? | Semester | Elective |
|-------------|-------------|---|---|---|-----------------|------------------|----------|----------|
| 24CS32 | Digital Design | 3 | 0 | 1 | No | No | 3 | No |

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
1. Fork the repository
2. Create your branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
