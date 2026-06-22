import streamlit as st
import pandas as pd
from db import get_db

@st.cache_data(show_spinner=False)
def generate_pdf_cached(timetables_dict, fac_tt_dict):
    from engine.pdf_export import create_timetables_pdf
    return create_timetables_pdf(timetables_dict, fac_tt_dict)

def style_section_cell(v):
    return "background-color: #e8f5e9; color: black;" if v else "background-color: #f5f5f5; color: black;"

def style_fac_cell(v):
    return "background-color: #e3f2fd; color: black;" if v else "background-color: #f5f5f5; color: black;"

st.set_page_config(page_title="Generate Timetable", page_icon="🚀", layout="wide")

st.title("🚀 Generate Timetable")
st.markdown("Run the CP-SAT constraint solver to generate an optimal timetable.")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SLOTS = ["S1\n9:00–9:55", "S2\n9:55–10:50", "S3\n11:05–12:00",
         "S4\n12:00–12:50", "S5\n1:45–2:40", "S6\n2:40–3:35", "S7\n3:35–4:30"]

# -----------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------
st.header("📋 Pre-flight Check")

db = get_db()
checks_ok = True

# Check courses
courses_count = db["courses"].count_documents({})
if courses_count > 0:
    st.success(f"✅ **Courses:** {courses_count} courses loaded")
else:
    st.error("❌ **Courses:** No courses found — upload data on the Input Data page first.")
    checks_ok = False

# Check faculty
semester = st.radio("Semester", ["Odd", "Even"], horizontal=True, key="gen_sem")
fac_col_name = f"faculty_{semester.lower()}"
fac_count = db[fac_col_name].count_documents({})
if fac_count > 0:
    st.success(f"✅ **Faculty ({semester}):** {fac_count} records loaded")
else:
    st.error(f"❌ **Faculty ({semester}):** No faculty records — upload data first.")
    checks_ok = False

# Check constraints
constraints_doc = db["constraints"].find_one({"type": "special_subjects"})
if constraints_doc:
    oe_count = len(constraints_doc.get("open_electives", []))
    aec_count = len(constraints_doc.get("aec", []))
    maths_count = len([m for m in constraints_doc.get("maths_slots", []) if m.get("Class")])
    st.success(f"✅ **Constraints:** {oe_count} OE, {aec_count} AEC, {maths_count} maths locks")
else:
    st.warning("⚠️ **Constraints:** Not configured — solver will run without special subject rules.")

st.divider()

# -----------------------------------------------------------------------
# Solver settings
# -----------------------------------------------------------------------
st.header("⚙️ Solver Settings")
col1, col2 = st.columns(2)
with col1:
    time_limit = st.number_input(
        "Solver time limit (seconds)",
        min_value=10,
        max_value=86400,
        value=60,
        step=10,
        help="How long the solver is allowed to run. Longer = better quality result.",
    )
with col2:
    st.info(f"Solver will stop after **{time_limit}s** and return the best solution found.")

st.divider()

# -----------------------------------------------------------------------
# Generate button
# -----------------------------------------------------------------------
if not checks_ok:
    st.warning("⚠️ Fix the issues above before generating.")
    st.stop()

if st.button("🚀 Generate Timetable", type="primary", use_container_width=True):
    progress_bar = st.progress(0.0, text="Building model and solving... (0s elapsed)")
    
    import threading
    import time
    
    result_container = {}
    def run_solver():
        try:
            from engine.solver import build_and_solve
            result_container["result"] = build_and_solve(
                semester=semester.lower(),
                time_limit_seconds=time_limit,
            )
        except Exception as e:
            import traceback
            result_container["error"] = str(e)
            result_container["traceback"] = traceback.format_exc()

    solver_thread = threading.Thread(target=run_solver)
    solver_thread.start()
    
    start_time = time.time()
    while solver_thread.is_alive():
        elapsed = int(time.time() - start_time)
        # Cap progress at 99% until thread finishes
        pct = min(elapsed / time_limit, 0.99)
        progress_bar.progress(pct, text=f"Building model and solving... ({elapsed}s elapsed)")
        time.sleep(0.5)
        
    solver_thread.join()
    final_elapsed = int(time.time() - start_time)
    progress_bar.progress(1.0, text=f"Finished in {final_elapsed}s!")
    
    if "error" in result_container:
        st.error(f"Solver crashed: {result_container['error']}")
        st.code(result_container["traceback"])
        st.stop()

    # Store result in session state
    st.session_state["solver_result"] = result_container["result"]

# -----------------------------------------------------------------------
# Display results (from session state)
# -----------------------------------------------------------------------
if "solver_result" not in st.session_state:
    st.info("👆 Click **Generate Timetable** to start the solver.")
    st.stop()

result = st.session_state["solver_result"]
status = result["status"]
stats = result["stats"]

# Status badge
st.header("📊 Results")
if status == "OPTIMAL":
    st.success(f"✅ **OPTIMAL** — Mathematically proven best timetable found in {stats.get('solve_time_s', '?')}s")
elif status == "FEASIBLE":
    st.warning(
        f"⚠️ **FEASIBLE** — A valid timetable was found within the time limit ({stats.get('solve_time_s', '?')}s), "
        f"but the solver did not prove it is the absolute best. "
        f"Try increasing the time limit for a higher-quality result."
    )
elif status == "INFEASIBLE":
    st.error("❌ **INFEASIBLE** — no valid timetable exists with current data & constraints.")
else:
    st.error(f"❌ Status: **{status}**")

# Stats
with st.expander("Solver Statistics"):
    stats_df = pd.DataFrame([stats])
    st.dataframe(stats_df, use_container_width=True)

# Errors
if result.get("errors"):
    for err in result["errors"]:
        st.error(err)

# -----------------------------------------------------------------------
# Section timetables
# -----------------------------------------------------------------------
timetables = result.get("timetables", {})
if timetables:
    st.subheader("🗓️ Section Timetables")
    selected_sec = st.selectbox("Select Section to View", sorted(timetables.keys()))
    if selected_sec:
        grid = timetables[selected_sec]  # 5 rows (days) × 7 cols (slots)
        df = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
        st.dataframe(
            df.style.map(style_section_cell),
            use_container_width=True,
            height=250,
        )

# -----------------------------------------------------------------------
# Faculty timetables
# -----------------------------------------------------------------------
fac_tt = result.get("faculty_timetables", {})
if fac_tt:
    st.subheader("👩‍🏫 Faculty Timetables")
    selected_fac = st.selectbox("Select Faculty to View", sorted(fac_tt.keys()))
    if selected_fac:
        grid = fac_tt[selected_fac]
        df = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
        st.dataframe(
            df.style.map(style_fac_cell),
            use_container_width=True,
            height=250,
        )

# -----------------------------------------------------------------------
# Export to PDF
# -----------------------------------------------------------------------
st.divider()
if timetables:
    col_export, col_clear = st.columns(2)
    with col_export:
        try:
            pdf_bytes = generate_pdf_cached(timetables, fac_tt)
            
            st.download_button(
                label="📄 Export to PDF",
                data=pdf_bytes,
                file_name=f"Timetables_{semester}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"Failed to generate PDF: {e}")
            
    with col_clear:
        if st.button("🗑️ Clear Results", use_container_width=True):
            del st.session_state["solver_result"]
            st.rerun()
