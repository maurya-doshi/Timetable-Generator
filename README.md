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

# 🗓️ Automated Timetable Generator

[![Streamlit Demo](https://img.shields.io/badge/Demo-Streamlit-brightgreen)](https://timetable-generator-1-u6sa.onrender.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A robust, AI-powered timetable generator designed for academic institutions. Built with **Streamlit** for the frontend and Google's **OR-Tools (CP-SAT)** for the backend, this application optimizes complex constraints to automatically schedule lectures, tutorials, and practical labs without clashes.

🚀 **Live Demo:** [https://timetable-generator-1-u6sa.onrender.com](https://timetable-generator-1-u6sa.onrender.com)

---

## ✨ Features

- **Mathematical Optimization:** Uses Constraint Programming (CP-SAT) to mathematically prove the best possible schedule.
- **Dynamic Constraints:** 
  - Teacher workload caps & availability.
  - Room allocation & double-booking prevention.
  - Contiguous multi-slot assignments for labs and tutorials.
- **Smart Penalty System:** Soft constraints actively minimize "bad" schedule behaviors (like repeating a subject in the first slot every day or spreading out a teacher's schedule too thinly).
- **Export to PDF:** Generates clean, formatted PDF timetables for sections and faculty using ReportLab.
- **Cloud Database:** Connects to MongoDB Atlas to persist generated timetables remotely.

## 🛠️ Technology Stack

- **Frontend:** [Streamlit](https://streamlit.io/)
- **Solver Engine:** [Google OR-Tools](https://developers.google.com/optimization/cp/cp_solver) (Constraint Programming)
- **Database:** [MongoDB](https://www.mongodb.com/) (PyMongo)
- **PDF Generation:** [ReportLab](https://pypi.org/project/reportlab/)
- **Data Handling:** [Pandas](https://pandas.pydata.org/) & [OpenPyXL](https://openpyxl.readthedocs.io/)

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- A MongoDB cluster (local or Atlas)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/maurya-doshi/Timetable-Generator.git
   cd Timetable-Generator
   ```

2. **Set up a virtual environment (optional but recommended):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables:**
   Create a `.env` file in the root directory and add your MongoDB connection string (optional, defaults to local):
   ```env
   MONGO_URI=mongodb+srv://<username>:<password>@cluster0...
   DB_NAME=timetable_generator
   ```

### Usage

1. Run the Streamlit application:
   ```bash
   streamlit run app.py
   ```
2. Open your browser to `http://localhost:8501`.
3. Upload your section, faculty, and room data (via the Excel templates provided in the app).
4. Click **Generate Timetable** and let the CP-SAT engine build your schedule!

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
1. Fork the repository
2. Create your branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
