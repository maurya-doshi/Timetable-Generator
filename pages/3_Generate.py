import streamlit as st
import pandas as pd
from db import get_db

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
    time_limit = st.slider("Solver time limit (seconds)", 10, 300, 60, step=10)
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
    with st.spinner("Building model and solving… this may take a minute."):
        try:
            from engine.solver import build_and_solve
            result = build_and_solve(
                semester=semester.lower(),
                time_limit_seconds=time_limit,
            )
        except Exception as e:
            st.error(f"Solver crashed: {e}")
            import traceback
            st.code(traceback.format_exc())
            st.stop()

    # Store result in session state
    st.session_state["solver_result"] = result

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
    st.success(f"✅ **OPTIMAL** solution found in {stats.get('solve_time_s', '?')}s")
elif status == "FEASIBLE":
    st.warning(f"⚠️ **FEASIBLE** (not proven optimal) — found in {stats.get('solve_time_s', '?')}s")
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
    section_tabs = st.tabs(sorted(timetables.keys()))
    for tab, sec in zip(section_tabs, sorted(timetables.keys())):
        with tab:
            grid = timetables[sec]  # 5 rows (days) × 7 cols (slots)
            df = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
            st.dataframe(
                df.style.map(
                    lambda v: "background-color: #e8f5e9" if v else "background-color: #f5f5f5"
                ),
                use_container_width=True,
                height=250,
            )

# -----------------------------------------------------------------------
# Faculty timetables
# -----------------------------------------------------------------------
fac_tt = result.get("faculty_timetables", {})
if fac_tt:
    st.subheader("👩‍🏫 Faculty Timetables")
    fac_tabs = st.tabs(sorted(fac_tt.keys()))
    for tab, fac in zip(fac_tabs, sorted(fac_tt.keys())):
        with tab:
            grid = fac_tt[fac]
            df = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
            st.dataframe(
                df.style.map(
                    lambda v: "background-color: #e3f2fd" if v else "background-color: #f5f5f5"
                ),
                use_container_width=True,
                height=250,
            )

# -----------------------------------------------------------------------
# Save to database
# -----------------------------------------------------------------------
st.divider()
if timetables:
    col_save, col_clear = st.columns(2)
    with col_save:
        if st.button("💾 Save Timetable to Database", type="primary"):
            try:
                tt_col = db["timetables"]
                doc = {
                    "semester": semester.lower(),
                    "status": status,
                    "section_timetables": timetables,
                    "faculty_timetables": fac_tt,
                    "stats": stats,
                }
                tt_col.delete_many({"semester": semester.lower()})
                tt_col.insert_one(doc)
                st.success("✅ Timetable saved to database!")
            except Exception as e:
                st.error(f"Failed to save: {e}")
    with col_clear:
        if st.button("🗑️ Clear Results"):
            del st.session_state["solver_result"]
            st.rerun()
