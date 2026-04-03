# 📅 Timetable Generator

A constraint-based timetable generator that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints. Built for a Computer Science department.

## Features

- **Excel Upload** — Upload faculty assignments and course data via `.xlsx` files
- **Faculty Management** — Parse two-row header format with subject and lab assignments per semester
- **Course Management** — Import courses with L/T/P hours, lab flags, and elective markers
- **Semester Support** — Separate Odd/Even semester faculty collections
- **MongoDB Storage** — Persist all data to MongoDB Atlas
- **Delete & Reset** — Full CRUD with confirmation dialogs

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit |
| Database | MongoDB Atlas + PyMongo |
| Excel Parsing | openpyxl |
| Constraint Solver | python-constraint / OR-Tools CP-SAT (planned) |

## Getting Started

### Prerequisites

- Python 3.11+
- MongoDB Atlas account (or local MongoDB)

### Installation

```bash
# Clone the repository
git clone https://github.com/maurya-doshi/Timetable-Generator.git
cd Timetable-Generator

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
# Create a .env file with:
# MONGO_URI=mongodb+srv://<user>:<pass>@cluster.mongodb.net/timetable
# DB_NAME=timetable_generator
```

### Running

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501`.

## Excel File Format

The app expects a `.xlsx` file with **two sheets**:

### 1. Faculty_Assignments (two-row header)

| Sr No. | Name | Designation | Subject | | | | | | Lab | | | |
|--------|------|-------------|---------|---|---|---|---|---|-----|---|---|---|
| | | | S1 | Sem S1 | S2 | Sem S2 | S3 | Sem S3 | L1 | Sem L1 | L2 | Sem L2 |
| 1 | Dr. X | Professor | PPC | 1 | DBMS | 5 | | | DBMS LAB | 5 | | |

### 2. Courses (single-row header)

| Course Code | Course Name | L | T | P | Lecture in Lab? | Tutorial in Lab? | Semester | Elective |
|-------------|-------------|---|---|---|-----------------|------------------|----------|----------|
| 24CS32 | Digital Design and Computer Organization | 3 | 0 | 1 | No | No | 3 | No |

A `Template.xlsx` and `Test.xlsx` are included in the repository for reference.

## Project Structure

```
Timetable-Generator/
├── app.py                   # Streamlit entry point & landing page
├── pages/
│   └── 1_Input_Data.py      # Faculty & course data upload
├── db.py                    # PyMongo connection helper
├── config.py                # Environment config
├── requirements.txt         # Python dependencies
├── Template.xlsx            # Blank Excel template
└── Test.xlsx                # Sample test data
```

## Roadmap

- [x] Page 1: Input Data (Excel upload, parsing, MongoDB save/delete)
- [ ] Page 2: Constraints (define scheduling rules)
- [ ] Page 3: Generate (run constraint solver)
- [ ] Page 4: Export (download timetable as PDF/Excel)

## License

This project is for academic use.