# Timetable Generator — Tech Stack

## Overview

A **constraint-based timetable generator** that automatically schedules classes, rooms, and teachers while satisfying hard and soft constraints. Built entirely in **Python** with a focus on simplicity.

---

## Core Stack

| Layer | Technology | Why |
|---|---|---|
| **Language** | Python 3.11+ | Primary language |
| **UI** | Streamlit | Pure Python — no HTML/CSS/JS needed, built-in widgets, free cloud hosting |
| **Database** | MongoDB Atlas + PyMongo | Free cloud DB, accessible from anywhere, simple driver |
| **Constraint Solver** | Google OR-Tools (CP-SAT) | Single library handles both hard & soft constraints |

---

## Why These Choices?

### 1. Streamlit (instead of Flask + Jinja2)

- **No frontend code at all** — UI is written entirely in Python.
- Built-in data tables, forms, charts, and interactive widgets.
- One command to run locally: `streamlit run app.py`
- **Free deployment** on [Streamlit Community Cloud](https://streamlit.io/cloud).
- Hot-reload on file save — fast development loop.

### 2. MongoDB Atlas + PyMongo (instead of MongoEngine)

- **MongoDB Atlas** free tier gives you a cloud database with zero setup.
- **PyMongo** is the official MongoDB driver — simple dict-in, dict-out, no ORM overhead.
- Document model fits timetable data naturally (nested schedules, flexible constraints).

### 3. Google OR-Tools CP-SAT (instead of python-constraint + DEAP)

- **One solver** handles everything — no need to maintain separate CSP + Genetic Algorithm code.
- CP-SAT supports both hard constraints (teacher clashes, room conflicts) and soft constraints (preferences, load balancing) through objective optimization.
- Battle-tested by Google, handles large problem sizes efficiently.
- Much simpler API than managing a custom genetic algorithm.

---

## Hard vs Soft Constraints

| Type | Examples | How CP-SAT Handles It |
|---|---|---|
| **Hard** (must satisfy) | No double-booking teachers/rooms, room capacity, mandatory hours | Added as strict constraints — solver rejects violations |
| **Soft** (optimize) | Teacher preferences, minimize student gaps, even subject spread | Added to objective function — solver maximizes/minimizes a score |

---

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
├── requirements.txt         # Python dependencies
└── TECH_STACK.md
```

---

## Python Packages

| Package | Purpose |
|---|---|
| `streamlit` | UI framework |
| `pymongo` | MongoDB driver |
| `ortools` | Google OR-Tools (CP-SAT solver) |
| `openpyxl` | Excel export |
| `reportlab` | PDF export |
| `python-dotenv` | Environment variable management |

---

## Getting Started (preview)

```bash
# Install dependencies
pip install -r requirements.txt

# Set MongoDB connection string
echo "MONGO_URI=mongodb+srv://<user>:<pass>@cluster.mongodb.net/timetable" > .env

# Run the app
streamlit run app.py
```
