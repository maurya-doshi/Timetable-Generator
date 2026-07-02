import streamlit as st

st.set_page_config(
    page_title="Timetable Generator",
    page_icon="📅",
    layout="wide",
)

st.title("📅 Timetable Generator")
st.markdown("*Constraint-based academic timetable scheduling powered by Google OR-Tools CP-SAT.*")

st.divider()

# ---------------------------------------------------------------------------
# Live database status
# ---------------------------------------------------------------------------
st.subheader("📊 Database Status")

try:
    from db import get_db
    db = get_db()

    courses_n    = db["courses"].count_documents({})
    fac_odd_n    = db["faculty_odd"].count_documents({})
    fac_even_n   = db["faculty_even"].count_documents({})
    constraints_doc = db["constraints"].find_one({"type": "special_subjects"}) or {}
    oe_n    = len(constraints_doc.get("open_electives", []))
    aec_n   = len(constraints_doc.get("aec", []))
    saved_n = db["timetables"].count_documents({})

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Courses",          courses_n,  help="Total courses in the database")
    col2.metric("Faculty (Odd)",    fac_odd_n,  help="Odd semester faculty records")
    col3.metric("Faculty (Even)",   fac_even_n, help="Even semester faculty records")
    col4.metric("OE + AEC",         f"{oe_n} / {aec_n}", help="Configured Open Elective / AEC subjects")
    col5.metric("Saved Results",    saved_n,    help="Previously generated timetables in DB")

    if courses_n == 0 or (fac_odd_n == 0 and fac_even_n == 0):
        st.warning("⚠️ No data uploaded yet. Start with **Step 1** below.")
    else:
        st.success("✅ Data is ready. Head to **Generate** to run the solver.")

except Exception as e:
    st.warning(f"⚠️ Could not connect to database: {e}")

st.divider()

# ---------------------------------------------------------------------------
# Step-by-step guide
# ---------------------------------------------------------------------------
st.subheader("🗺️ Workflow")

col_a, col_b, col_c, col_d = st.columns(4)

with col_a:
    st.markdown("""
    ### ⚙️ Step 0 — Settings
    Configure your **section structure** (which sections exist per semester),
    set the academic year used in exports, and adjust solver defaults.

    *Only needed when your batch structure changes.*
    """)

with col_b:
    st.markdown("""
    ### 📋 Step 1 — Input Data
    Upload a single **Excel file** with two sheets:
    - `Faculty_Assignments` — who teaches what
    - `Courses` — L/T/P hours, elective flags

    Faculty codes are cross-validated against courses automatically.
    """)

with col_c:
    st.markdown("""
    ### ⚙️ Step 2 — Constraints
    Tag your **Open Electives**, **AEC** subjects, and **PG shared** classes.
    Lock **Maths** slots and reserve **CSE Lab** blocks for 1st/2nd semester.
    """)

with col_d:
    st.markdown("""
    ### 🚀 Step 3 — Generate
    Run the **CP-SAT solver**. View results with:
    - Per-section & per-faculty timetables
    - Live solver log & workload summary
    - Diff against previous run
    - Export to **PDF** or **Excel**
    """)

st.divider()

# ---------------------------------------------------------------------------
# Constraint summary
# ---------------------------------------------------------------------------
with st.expander("📜 Active Constraint Summary (H1–H16)", expanded=False):
    st.markdown("""
    | # | Constraint | Description |
    |---|-----------|-------------|
    | H1 | Faculty double-booking | No faculty in two places at once (includes co-faculty) |
    | H2 | Section double-booking | No section attending two classes simultaneously |
    | H3 | Weekly hours | Exactly L lectures + T tutorial block + P practical block(s) per course |
    | H4 | No student gaps | No free slot sandwiched between two occupied slots (except lunch) |
    | H5 | Morning first | Morning slots (S1–S4) filled before afternoon (S5–S7) |
    | H5.5 | No empty teaching days | Every teaching day has at least one morning slot occupied |
    | H6 | Faculty break | Mandatory 1-slot gap between any two faculty teaching events |
    | H7 | OE concurrency | All sections sharing an Open Elective attend at the same time |
    | H8 | AEC concurrency | All 3rd-sem (and all 4th-sem) sections share one AEC slot |
    | H9 | PG shared core | SP-1 and SP-2 attend the shared core lecture together |
    | H10 | Maths locks | Maths lectures/tutorials fixed to user-defined (day, slot) positions |
    | H11 | CSE lab locks | Selected (day, slot, room) blocks reserved for 1st/2nd sem CSE labs |
    | H12 | Subject spread | At most 1 event per (section, course) per day — no bunching |
    | H13 | No S1 repeat | A course occupies the first slot (9:00 AM) on at most one day/week |
    | H14 | Lab room | Each practical/tutorial block assigned to exactly one of 4 CSE labs |
    | H15 | Friday half-day | No afternoon classes (S5–S7) on Fridays |
    | H16 | Workload cap | Professor ≤ 18 units, Associate ≤ 24, Assistant ≤ 28 (odd sem) |
    """)
