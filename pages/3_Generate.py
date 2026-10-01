"""
3_Generate.py -- Timetable generation page.

Features (improvements implemented here):
    #1  Excel export
    #2  Pre-flight check panel
    #3  Timetable diff vs previous run
    #4  View All Sections / All Faculty tabs
    #5  Faculty workload summary
    #6  Live solver log via progress queue
    #7  Persist results to MongoDB + load previous
    #10 Pass section_map from Settings to solver
    #11 Infeasibility diagnostic hints
"""

import queue
import threading
import time

import numpy as np
import pandas as pd
import streamlit as st

from db import (
    get_db,
    get_section_map,
    get_settings,
    save_timetable_result,
    list_timetable_results,
    load_timetable_result,
    delete_timetable_result,
)


# ---------------------------------------------------------------------------
# Cached DB helpers — fetched once per app process, not on every rerun.
# @st.cache_resource caches across all sessions; TTL=300 means a 5-min
# staleness window which is fine for settings that rarely change.
# ---------------------------------------------------------------------------
@st.cache_resource(ttl=300, show_spinner=False)
def _cached_settings():
    """Return settings + section_map in one Atlas round-trip."""
    try:
        s = get_settings()
        sm = get_section_map()
        return s, sm
    except Exception:
        return {}, None


@st.cache_data(ttl=30, show_spinner=False)
def _cached_preflight_data(semester: str):
    """Cache the raw DB counts and constraints doc for 30 s.

    30 s is long enough to cover the entire solving polling loop
    (1.5 s/rerun) without re-hitting Atlas, but short enough that
    data is fresh when the user first loads the page.
    """
    try:
        db = get_db()
        courses_count = db["courses"].count_documents({})
        fac_count     = db[f"faculty_{semester}"].count_documents({})
        constraints_doc = db["constraints"].find_one({"type": "special_subjects"})
        return courses_count, fac_count, constraints_doc
    except Exception as e:
        return 0, 0, None


@st.cache_data(ttl=60, show_spinner=False)
def _cached_prev_list(semester: str):
    """Cache the saved-results list for 60 s."""
    try:
        return list_timetable_results(semester)
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _cached_preflight_run(semester: str, section_map_key: str):
    """Cache the structural preflight result for 60 s.

    section_map_key is a string hash of the section_map so cache
    invalidates if settings change.
    """
    try:
        from engine.preflight import load_and_run as _run_pf
        import ast
        section_map = ast.literal_eval(section_map_key) if section_map_key else None
        return _run_pf(semester, section_map=section_map)
    except Exception as e:
        return {"errors": [], "warnings": [], "ok": True}

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Generate Timetable", page_icon="🚀", layout="wide")

DAYS  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SLOTS = [
    "S1\n9:00–9:55", "S2\n9:55–10:50", "S3\n11:05–12:00",
    "S4\n12:00–12:50", "L1\n12:50–1:45", "S5\n1:45–2:40", "S6\n2:40–3:35", "S7\n3:35–4:30",
]

# ---------------------------------------------------------------------------
# Cached export helpers
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _cached_pdf(timetables_dict, fac_tt_dict, semester="", academic_year=""):
    from engine.pdf_export import create_timetables_pdf
    return create_timetables_pdf(timetables_dict, fac_tt_dict, semester=semester, academic_year=academic_year)


@st.cache_data(show_spinner=False)
def _cached_excel(timetables_dict, fac_tt_dict, academic_year=""):
    from engine.excel_export import create_timetables_excel
    return create_timetables_excel(timetables_dict, fac_tt_dict, academic_year)


# ---------------------------------------------------------------------------
# Cell styling helpers
# ---------------------------------------------------------------------------
def _style_sec(v):
    return "background-color:#e8f5e9;color:black;" if v else "background-color:#f5f5f5;color:black;"

def _style_fac(v):
    return "background-color:#e3f2fd;color:black;" if v else "background-color:#f5f5f5;color:black;"

def _pad_grid(grid):
    padded = []
    for row in grid:
        if len(row) < len(SLOTS):
            row = row + [""] * (len(SLOTS) - len(row))
        elif len(row) > len(SLOTS):
            row = row[:len(SLOTS)]
        padded.append(row)
    return padded

def _make_diff_styles(data: pd.DataFrame, changed: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame of CSS strings for use with style.apply(axis=None)."""
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    for r in range(len(data)):
        for c in range(len(data.columns)):
            if changed.iloc[r, c]:
                styles.iloc[r, c] = "background-color:#fff9c4;color:black;"
            else:
                styles.iloc[r, c] = (
                    "background-color:#e8f5e9;color:black;"
                    if data.iloc[r, c] else "background-color:#f5f5f5;color:black;"
                )
    return styles


# ---------------------------------------------------------------------------
# Load settings & section map (cached — one Atlas call per 5 min max)
# ---------------------------------------------------------------------------
_settings, _section_map = _cached_settings()
_acad_year   = _settings.get("academic_year", "")
_def_tlimit  = int(_settings.get("default_time_limit", 60))
_def_workers = int(_settings.get("default_workers", 8))

# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------
st.title("🚀 Generate Timetable")
st.markdown("Run the CP-SAT constraint solver to generate an optimal timetable.")

# ---------------------------------------------------------------------------
# Semester selection (needed early for pre-flight and DB queries)
# ---------------------------------------------------------------------------
semester = st.radio("Semester", ["Odd", "Even"], horizontal=True, key="gen_sem")

# ===========================================================================
# PRE-FLIGHT CHECK (#2)
# Skip all DB reads during the solver polling loop — data doesn't change
# while the solver is running and each rerun should be as cheap as possible.
# ===========================================================================
st.header("📋 Pre-flight Check")

_is_solving = st.session_state.get("_solver_running", False)
checks_ok   = True

if _is_solving:
    # During solving: reuse cached values from session_state set before launch
    courses_count   = st.session_state.get("_pf_courses_count", 1)
    fac_count       = st.session_state.get("_pf_fac_count", 1)
    constraints_doc = st.session_state.get("_pf_constraints_doc")
    _pf_cached      = st.session_state.get("_pf_result", {"ok": True, "errors": [], "warnings": []})
else:
    # Normal load: hit Atlas (but results are cached for 30s)
    courses_count, fac_count, constraints_doc = _cached_preflight_data(semester.lower())
    _sm_key = str(_section_map) if _section_map else ""
    _pf_cached = _cached_preflight_run(semester.lower(), _sm_key)

# ---- Display counts ----
if courses_count > 0:
    st.success(f"✅ **Courses:** {courses_count} courses loaded")
else:
    st.error("❌ **Courses:** No courses found — upload data on the Input Data page first.")
    checks_ok = False

if fac_count > 0:
    st.success(f"✅ **Faculty ({semester}):** {fac_count} records loaded")
else:
    st.error(f"❌ **Faculty ({semester}):** No faculty records — upload data first.")
    checks_ok = False

if constraints_doc:
    oe_cnt    = len(constraints_doc.get("open_electives", []))
    aec_cnt   = len(constraints_doc.get("aec", []))
    pec_cnt   = len(constraints_doc.get("pec", []))
    maths_cnt = len([m for m in constraints_doc.get("maths_slots", []) if m.get("Class")])
    hod_name  = constraints_doc.get("hod")
    hod_str   = f", HOD: {hod_name}" if hod_name else ""
    st.success(f"✅ **Constraints:** {oe_cnt} OE, {aec_cnt} AEC, {pec_cnt} PEC, {maths_cnt} maths locks{hod_str}")
else:
    st.warning("⚠️ **Constraints:** Not configured — solver will run without special subject rules.")

# ---- Structural preflight ----
if checks_ok:
    if _pf_cached.get("errors"):
        for err in _pf_cached["errors"]:
            st.error(f"❌ {err}")
        checks_ok = False
    for warn in _pf_cached.get("warnings", []):
        st.warning(f"⚠️ {warn}")
    if _pf_cached.get("ok") and not _pf_cached.get("warnings"):
        st.success("✅ **Structural checks:** All passed.")
    elif _pf_cached.get("ok"):
        st.info("ℹ️ Preflight passed with warnings. The solver may still find a solution.")

st.divider()

# ===========================================================================
# SOLVER SETTINGS
# ===========================================================================
st.header("⚙️ Solver Settings")
col1, col2 = st.columns(2)
with col1:
    time_limit = st.number_input(
        "Solver time limit (seconds)",
        min_value=10, max_value=86400,
        value=_def_tlimit, step=10,
        help="How long the solver is allowed to run. Longer = better quality result.",
    )
with col2:
    num_workers = st.number_input(
        "Parallel workers",
        min_value=1, max_value=32,
        value=_def_workers, step=1,
        help="OR-Tools parallel workers. Match to your CPU core count.",
    )
    st.info(f"Solver will stop after **{time_limit}s** using **{num_workers}** workers.")

st.divider()

# ===========================================================================
# LOAD PREVIOUS RESULT (#7)
# Skip entirely during solving — not needed and costs an Atlas query.
# ===========================================================================
if not _is_solving:
    with st.expander("📂 Load a Previous Result", expanded=False):
        prev_list = _cached_prev_list(semester.lower())

        if not prev_list:
            st.info("No saved results found for this semester.")
        else:
            def _fmt(r):
                ts  = r.get("generated_at")
                ts_str = ts.strftime("%d %b %Y %H:%M") if ts else "?"
                st_str = r.get("status", "?")
                secs = r["stats"].get("num_sections", "?")
                t    = r["stats"].get("solve_time_s", "?")
                return f"{ts_str} — {st_str}  ({secs} sections, {t}s)"

            options = {_fmt(r): r["id"] for r in prev_list}
            selected_label = st.selectbox("Select result to load", list(options.keys()))
            selected_id    = options[selected_label]

            col_load, col_del = st.columns(2)
            with col_load:
                if st.button("📥 Load Selected Result", use_container_width=True):
                    loaded = load_timetable_result(selected_id)
                    if loaded:
                        # Archive current result before overwriting
                        if "solver_result" in st.session_state:
                            st.session_state["prev_solver_result"] = st.session_state["solver_result"]
                        st.session_state["solver_result"] = loaded
                        st.success("Result loaded.")
                        st.rerun()
                    else:
                        st.error("Could not load result.")
            with col_del:
                if st.button("🗑️ Delete Selected Result", use_container_width=True, type="secondary"):
                    ok = delete_timetable_result(selected_id)
                    if ok:
                        st.success("Result deleted.")
                        _cached_prev_list.clear()  # Invalidate the cache after delete
                        st.rerun()
                    else:
                        st.error("Could not delete.")

st.divider()

# ===========================================================================
# GENERATE BUTTON
# ===========================================================================
if not checks_ok:
    st.warning("⚠️ Fix the issues above before generating.")
    st.stop()

if st.button("🚀 Generate Timetable", type="primary", use_container_width=True):
    # Archive any existing result for diff comparison
    if "solver_result" in st.session_state:
        st.session_state["prev_solver_result"] = st.session_state["solver_result"]

    # -----------------------------------------------------------------------
    # Launch solver in a background thread and store state in session_state.
    # We do NOT block with a while-loop here — instead we use st.rerun() to
    # poll, keeping each Streamlit execution short and the WebSocket alive.
    # This fixes the HuggingFace ~150s session timeout.
    # -----------------------------------------------------------------------
    progress_q    = queue.Queue()
    result_holder = {}

    def _run_solver(result_holder, progress_q, semester, time_limit, num_workers, section_map):
        try:
            from engine.solver import build_and_solve
            result_holder["result"] = build_and_solve(
                semester=semester,
                time_limit_seconds=int(time_limit),
                num_workers=int(num_workers),
                section_map=section_map,
                progress_queue=progress_q,
            )
        except Exception as exc:
            import traceback
            result_holder["error"]     = str(exc)
            result_holder["traceback"] = traceback.format_exc()
        finally:
            result_holder["done"] = True

    solver_thread = threading.Thread(
        target=_run_solver,
        args=(result_holder, progress_q, semester.lower(), time_limit, num_workers, _section_map),
        daemon=True,
    )
    solver_thread.start()

    # Snapshot preflight data into session_state so polling reruns
    # can display it without hitting Atlas on every cycle.
    st.session_state["_pf_courses_count"]   = courses_count
    st.session_state["_pf_fac_count"]        = fac_count
    st.session_state["_pf_constraints_doc"]  = constraints_doc
    st.session_state["_pf_result"]           = _pf_cached

    # Store everything needed for the polling loop in session_state
    st.session_state["_solver_running"] = True
    st.session_state["_solver_thread"]  = solver_thread
    st.session_state["_solver_result_holder"] = result_holder
    st.session_state["_solver_progress_q"]    = progress_q
    st.session_state["_solver_start_t"]  = time.time()
    st.session_state["_solver_log_lines"] = []
    st.session_state["_solver_time_limit"] = int(time_limit)
    st.session_state["_solver_semester"]   = semester.lower()
    st.rerun()

# -----------------------------------------------------------------------
# Polling loop: each st.rerun() call is a short Streamlit cycle that keeps
# the WebSocket connection alive — no more 150s timeout on HuggingFace.
# -----------------------------------------------------------------------
if st.session_state.get("_solver_running"):
    solver_thread  = st.session_state["_solver_thread"]
    result_holder  = st.session_state["_solver_result_holder"]
    progress_q     = st.session_state["_solver_progress_q"]
    start_t        = st.session_state["_solver_start_t"]
    log_lines      = st.session_state["_solver_log_lines"]
    time_limit_ss  = st.session_state["_solver_time_limit"]
    _sem           = st.session_state["_solver_semester"]

    elapsed = int(time.time() - start_t)
    pct     = min(elapsed / max(time_limit_ss, 1), 0.99)

    progress_bar  = st.progress(pct, text=f"Solving... ({elapsed}s elapsed)")
    log_container = st.empty()

    # Drain any new log messages from the queue
    while not progress_q.empty():
        try:
            msg = progress_q.get_nowait()
            log_lines.append(msg["message"])
        except queue.Empty:
            break
    st.session_state["_solver_log_lines"] = log_lines

    if log_lines:
        log_container.code("\n".join(log_lines[-15:]), language=None)

    if result_holder.get("done") or not solver_thread.is_alive():
        # Solver finished — clean up and process result
        solver_thread.join(timeout=2)
        elapsed_final = int(time.time() - start_t)
        progress_bar.progress(1.0, text=f"Finished in {elapsed_final}s!")

        # Clear running state
        for k in ["_solver_running", "_solver_thread", "_solver_result_holder",
                  "_solver_progress_q", "_solver_start_t", "_solver_log_lines",
                  "_solver_time_limit", "_solver_semester"]:
            st.session_state.pop(k, None)

        if "error" in result_holder:
            st.error(f"Solver crashed: {result_holder['error']}")
            st.code(result_holder["traceback"])
            st.stop()

        result = result_holder["result"]
        st.session_state["solver_result"] = result

        # Auto-save to MongoDB (#7)
        if result.get("status") in ("OPTIMAL", "FEASIBLE"):
            try:
                save_timetable_result(_sem, result)
            except Exception:
                pass   # Don't fail the page if save fails

        st.rerun()  # Final rerun to display results cleanly
    else:
        # Still running — sleep briefly then rerun to poll again
        time.sleep(1.5)
        st.rerun()

# ===========================================================================
# RESULTS DISPLAY
# ===========================================================================
if "solver_result" not in st.session_state:
    st.info("👆 Click **Generate Timetable** to start the solver.")
    st.stop()

result = st.session_state["solver_result"]
status = result["status"]
stats  = result["stats"]

# ---- Status badge ----
st.header("📊 Results")
if status == "OPTIMAL":
    st.success(f"✅ **OPTIMAL** — Mathematically proven best timetable in {stats.get('solve_time_s','?')}s")
elif status == "FEASIBLE":
    st.warning(
        f"⚠️ **FEASIBLE** — Valid timetable found in {stats.get('solve_time_s','?')}s, "
        "but not proven optimal. Increase time limit for a higher-quality result."
    )
elif status == "INFEASIBLE":
    st.error("❌ **INFEASIBLE** — No valid timetable exists with the current data & constraints.")
else:
    st.error(f"❌ Status: **{status}**")

# ---- Solver stats expander ----
with st.expander("🔢 Solver Statistics"):
    st.dataframe(pd.DataFrame([stats]), use_container_width=True)

# ---- Errors ----
for err in result.get("errors", []):
    st.error(err)

# ===========================================================================
# INFEASIBILITY HINTS (#11)
# ===========================================================================
hints = result.get("infeasibility_hints", [])
if hints:
    st.subheader("🔍 Infeasibility Diagnosis")
    st.markdown("The solver ran diagnostic passes to identify the likely cause:")
    for h in hints:
        st.markdown(f"- {h}")

timetables = result.get("timetables", {})
fac_tt     = result.get("faculty_timetables", {})
workload   = result.get("workload", {})

if not timetables:
    st.stop()

# ===========================================================================
# WORKLOAD SUMMARY (#5)
# ===========================================================================
if workload:
    with st.expander("📊 Faculty Workload Summary", expanded=False):
        rows = []
        for fac, w in sorted(workload.items()):
            pct = w.get("pct", 0)
            rows.append({
                "Faculty":     fac,
                "Designation": w.get("designation", ""),
                "Scheduled":   w["scheduled"],
                "Cap":         w["cap"],
                "Usage %":     pct,
                "Status":      "⚠️ Over cap" if w["scheduled"] > w["cap"] else ("✅ OK" if pct <= 90 else "🟡 Near cap"),
            })
        wdf = pd.DataFrame(rows)

        def _colour_status(v):
            if "Over" in str(v):
                return "background-color:#ffcdd2;color:black;"
            if "Near" in str(v):
                return "background-color:#fff9c4;color:black;"
            return "background-color:#e8f5e9;color:black;"

        styled_wdf = wdf.style.map(_colour_status, subset=["Status"])
        st.dataframe(styled_wdf, use_container_width=True, hide_index=True)

st.divider()

# ===========================================================================
# TIMETABLE TABS (#4 — All Sections / All Faculty)
# ===========================================================================
tab_sec, tab_all_sec, tab_fac, tab_all_fac = st.tabs([
    "👤 Single Section", "📋 All Sections",
    "👩‍🏫 Single Faculty", "👩‍🏫 All Faculty",
])

# ---- Single Section ----
with tab_sec:
    selected_sec = st.selectbox("Select Section to View", sorted(timetables.keys()), key="sel_sec")
    if selected_sec:
        grid = _pad_grid(timetables[selected_sec])
        df   = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
        st.dataframe(df.style.map(_style_sec), use_container_width=True, height=250)

# ---- All Sections ----
with tab_all_sec:
    st.markdown("*All section timetables — scroll down to see all.*")
    sec_cols = st.columns(2)
    for i, sec in enumerate(sorted(timetables.keys())):
        with sec_cols[i % 2]:
            st.subheader(f"Section {sec}")
            grid = _pad_grid(timetables[sec])
            df   = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
            st.dataframe(df.style.map(_style_sec), use_container_width=True, height=230)

# ---- Single Faculty ----
with tab_fac:
    selected_fac = st.selectbox("Select Faculty to View", sorted(fac_tt.keys()), key="sel_fac")
    if selected_fac:
        grid = _pad_grid(fac_tt[selected_fac])
        df   = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
        st.dataframe(df.style.map(_style_fac), use_container_width=True, height=250)

# ---- All Faculty ----
with tab_all_fac:
    st.markdown("*All faculty timetables — scroll down to see all.*")
    fac_cols = st.columns(2)
    for i, fac in enumerate(sorted(fac_tt.keys())):
        with fac_cols[i % 2]:
            st.subheader(fac)
            grid = _pad_grid(fac_tt[fac])
            df   = pd.DataFrame(grid, index=DAYS, columns=SLOTS)
            st.dataframe(df.style.map(_style_fac), use_container_width=True, height=230)

st.divider()

# ===========================================================================
# DIFF VS PREVIOUS (#3)
# ===========================================================================
prev_result = st.session_state.get("prev_solver_result")
if prev_result and prev_result.get("timetables"):
    with st.expander("🔄 Diff vs Previous Run", expanded=False):
        st.markdown("Yellow cells changed between the previous and current run.")

        diff_sec_options = sorted(
            set(timetables.keys()) | set(prev_result["timetables"].keys())
        )
        diff_sec = st.selectbox("Compare section", diff_sec_options, key="diff_sec")

        new_grid = _pad_grid(timetables.get(diff_sec, [[""] * 8] * 5))
        old_grid = _pad_grid(prev_result["timetables"].get(diff_sec, [[""] * 8] * 5))

        new_df = pd.DataFrame(new_grid, index=DAYS, columns=SLOTS)
        old_df = pd.DataFrame(old_grid, index=DAYS, columns=SLOTS)
        changed_df = new_df != old_df

        n_changed = int(changed_df.values.sum())
        if n_changed == 0:
            st.success("✅ No changes in this section between the two runs.")
        else:
            st.info(f"**{n_changed}** slot(s) changed for section **{diff_sec}**.")

        styled = new_df.style.apply(
            lambda _: _make_diff_styles(new_df, changed_df),
            axis=None,
        )
        st.dataframe(styled, use_container_width=True, height=250)

        col_prev, col_new = st.columns(2)
        with col_prev:
            st.caption("Previous run")
            st.dataframe(old_df.style.map(_style_sec), use_container_width=True, height=230)
        with col_new:
            st.caption("Current run")
            st.dataframe(new_df.style.map(_style_sec), use_container_width=True, height=230)

# ===========================================================================
# EXPORT BUTTONS (#1 — Excel + PDF)
# ===========================================================================
st.subheader("📤 Export")
col_pdf, col_excel, col_clear = st.columns(3)

with col_pdf:
    try:
        pdf_bytes = _cached_pdf(timetables, fac_tt, semester=semester, academic_year=_acad_year)
        st.download_button(
            label="📄 Export to PDF",
            data=pdf_bytes,
            file_name=f"Timetables_{semester}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"PDF export failed: {e}")

with col_excel:
    try:
        xlsx_bytes = _cached_excel(timetables, fac_tt, _acad_year)
        st.download_button(
            label="📊 Export to Excel",
            data=xlsx_bytes,
            file_name=f"Timetables_{semester}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"Excel export failed: {e}")

with col_clear:
    if st.button("🗑️ Clear Results", use_container_width=True):
        st.session_state.pop("solver_result", None)
        st.session_state.pop("prev_solver_result", None)
        st.rerun()
