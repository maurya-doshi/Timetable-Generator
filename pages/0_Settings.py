import streamlit as st
import pandas as pd
from db import get_db, get_settings, save_settings, get_section_map, _DEFAULT_SECTION_MAP

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")
st.markdown("Configure section structure, academic year, and solver defaults.")

# ---------------------------------------------------------------------------
# Load current settings
# ---------------------------------------------------------------------------
try:
    settings = get_settings()
    db_ok = True
except Exception as e:
    st.error(f"Could not connect to database: {e}")
    settings = {}
    db_ok = False

st.divider()

# ===========================================================================
# Section 1 — Section Map (#10)
# ===========================================================================
st.header("1. Section Configuration")
st.markdown(
    """
    Define which **sections** exist for each **semester**.
    Changes here take effect on the next solver run — existing uploaded data is not modified.

    *Default sections come from the built-in map. Edit the **Sections** column as a
    comma-separated list (e.g. `3A, 3B, 3C, 3D`).*
    """
)

current_map = settings.get("section_map") or dict(_DEFAULT_SECTION_MAP)

# Build editable dataframe
map_rows = [
    {"Semester": sem, "Sections": ", ".join(secs)}
    for sem, secs in sorted(current_map.items(), key=lambda x: int(x[0]))
]
map_df = pd.DataFrame(map_rows)

edited_map_df = st.data_editor(
    map_df,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "Semester": st.column_config.TextColumn("Semester", disabled=True, width="small"),
        "Sections": st.column_config.TextColumn(
            "Sections (comma-separated)",
            help="Enter section names separated by commas, e.g. 3A, 3B, 3C, 3D",
        ),
    },
    key="section_map_editor",
)

col_reset, col_spacer = st.columns([1, 3])
with col_reset:
    if st.button("↩️ Reset to Defaults", use_container_width=True):
        settings.pop("section_map", None)
        if db_ok:
            try:
                save_settings(settings)
                st.success("Section map reset to defaults.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save: {e}")

st.divider()

# ===========================================================================
# Section 2 — Academic Year
# ===========================================================================
st.header("2. Academic Year")
st.markdown("Used as a label in exported PDF and Excel filenames.")

academic_year = st.text_input(
    "Academic Year",
    value=settings.get("academic_year", ""),
    placeholder="e.g. 2025-26",
    max_chars=10,
)

st.divider()

# ===========================================================================
# Section 3 — Solver Defaults
# ===========================================================================
st.header("3. Solver Defaults")
st.markdown(
    "These values pre-fill the **Generate** page. They can always be overridden per-run."
)

col1, col2 = st.columns(2)
with col1:
    default_time_limit = st.number_input(
        "Default time limit (seconds)",
        min_value=10, max_value=86400,
        value=int(settings.get("default_time_limit", 60)),
        step=10,
    )
with col2:
    default_workers = st.number_input(
        "Default solver workers",
        min_value=1, max_value=32,
        value=int(settings.get("default_workers", 8)),
        step=1,
        help="Number of parallel CPU workers for OR-Tools CP-SAT. Set to your core count.",
    )

st.divider()

# ===========================================================================
# Save button
# ===========================================================================
if st.button("💾 Save Settings", type="primary", use_container_width=True):
    if not db_ok:
        st.error("Cannot save — database not connected.")
    else:
        # Parse section map from edited dataframe
        new_map = {}
        for _, row in edited_map_df.iterrows():
            sem  = str(row["Semester"]).strip()
            secs_raw = str(row["Sections"]).strip()
            secs = [s.strip() for s in secs_raw.split(",") if s.strip()]
            if sem and secs:
                new_map[sem] = secs

        if not new_map:
            st.error("Section map cannot be empty.")
        else:
            new_settings = {
                "section_map":         new_map,
                "academic_year":       academic_year.strip(),
                "default_time_limit":  int(default_time_limit),
                "default_workers":     int(default_workers),
            }
            try:
                save_settings(new_settings)
                st.success("✅ Settings saved successfully.")
                # Clear any cached section maps in session state
                for key in ["solver_result", "prev_solver_result"]:
                    st.session_state.pop(key, None)
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save settings: {e}")

# ===========================================================================
# Current effective section map preview
# ===========================================================================
with st.expander("📋 Current Effective Section Map (preview)"):
    try:
        effective = get_section_map()
        preview_rows = []
        for sem, secs in sorted(effective.items(), key=lambda x: int(x[0])):
            preview_rows.append({"Semester": sem, "Sections": ", ".join(secs), "Count": len(secs)})
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"Could not load section map: {e}")
