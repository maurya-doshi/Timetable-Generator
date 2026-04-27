import streamlit as st
import pandas as pd
from db import get_db

st.set_page_config(page_title="Constraints Builder", page_icon="⚙️", layout="wide")

st.title("⚙️ Constraints Builder")
st.markdown("Configure special scheduling rules and tag specific subjects to trigger hardcoded constraints.")

# Connect to database
db = get_db()
db.client.get_io_loop = db.client.get_io_loop if hasattr(db.client, 'get_io_loop') else None

try:
    db.client.admin.command('ping')
    courses_cursor = db["courses"].find({}, {"_id": 0})
    courses = list(courses_cursor)
except Exception:
    courses = [
        {"course_code": "CS301", "course_name": "Data Structures"},
        {"course_code": "CS304", "course_name": "AEC - EVS"},
        {"course_code": "PG101", "course_name": "Advanced DBMS (Core)"},
        {"course_code": "OE101", "course_name": "Open Elective - AI Base"},
    ]

course_names = [c.get("course_name", c.get("course_code", "Unknown")) for c in courses]
ug_courses = [c for c in courses if str(c.get("ug_pg", "UG")).upper() == "UG"]
pg_courses = [c for c in courses if str(c.get("ug_pg", "UG")).upper() == "PG"]

ug_course_names = [c.get("course_name", c.get("course_code", "Unknown")) for c in ug_courses]
pg_course_names = [c.get("course_name", c.get("course_code", "Unknown")) for c in pg_courses]

if not course_names:
    st.info("No courses found in the `courses` collection. Once the Master Subject List parsing is complete, they will appear here.", icon="🕒")

# --- Fetch existing configuration ---
current_config = {}
try:
    constraints_col = db["constraints"]
    current_config = constraints_col.find_one({"type": "special_subjects"}) or {}
except Exception:
    pass

# --- Fetch existing configuration for PG ---
default_pg_core = current_config.get("pg_shared_core", "None")
if default_pg_core not in pg_course_names:
    default_pg_core = "None"

default_pg_pe = current_config.get("pg_shared_pe", [])
default_pg_pe = [x for x in default_pg_pe if x in pg_course_names]

# --- Auto-calculate AEC and OE from courses data ---
computed_oe_data = [c for c in courses 
               if str(c.get("semester")).strip() in ["5", "6", "7"] 
               and str(c.get("elective", "No")).lower() in ["yes", "y", "true"] 
               and str(c.get("ug_pg", "UG")).upper() == "UG"]

computed_oe = [c.get("course_name", c.get("course_code")) for c in computed_oe_data]
computed_oe_display = [f"{c.get('course_name')} (Sem {c.get('semester')})" for c in computed_oe_data]

computed_aec_data = [c for c in courses 
                if str(c.get("semester")).strip() in ["3", "4"]
                and str(c.get("aec", "No")).lower() in ["yes", "y", "true"]]

computed_aec = [c.get("course_name", c.get("course_code")) for c in computed_aec_data]
computed_aec_display = [f"{c.get('course_name')} (Sem {c.get('semester')})" for c in computed_aec_data]

st.header("1. Subject Constraints")
ug_tab, pg_tab = st.tabs(["🎓 Undergraduate (UG)", "🏫 Postgraduate (PG)"])

with ug_tab:
    st.subheader("Automated UG Subject Identifiers")
    st.markdown("We have automatically identified your Open Electives and AEC subjects from the Excel file.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("**Open Electives (OE)**\n\n*(Scheduled concurrently on **Monday, Tuesday, Wednesday, 5th Slot** for 5th/6th/7th Sem)*")
        if computed_oe_display:
            st.dataframe(pd.DataFrame({"Course Name & Semester": computed_oe_display}), use_container_width=True, hide_index=True)
        else:
            st.write("None detected")

    with col2:
        st.info("**Ability Enhancement Course (AEC)**\n\n*(Scheduled at the **same time** for all 3rd & 4th Sem sections)*")
        if computed_aec_display:
            st.dataframe(pd.DataFrame({"Course Name & Semester": computed_aec_display}), use_container_width=True, hide_index=True)
        else:
            st.write("None detected")

    st.write("---")
    st.markdown("**Are the automatically identified AEC and OE subjects correct?**")
    verify_choice = st.radio(
        label="Verify Automation",
        options=["🟢 Yes, use these automatically identified subjects", "🔴 No, let me select manually"],
        horizontal=True,
        label_visibility="collapsed"
    )

    if "Yes" in verify_choice:
        selected_oes = computed_oe
        selected_aec = computed_aec
    else:
        st.warning("Manual Override Enabled. Please select the correct UG subjects below:")
        col_a, col_b = st.columns(2)
        with col_a:
            selected_oes = st.multiselect("Open Elective Subjects", options=ug_course_names, default=[x for x in computed_oe if x in ug_course_names])
        with col_b:
            selected_aec = st.multiselect("AEC Subjects", options=ug_course_names, default=[x for x in computed_aec if x in ug_course_names])

with pg_tab:
    st.subheader("PG Shared Classes (SP-1 & SP-2)")
    st.markdown("*(Select specific PG subjects taught by the same faculty that must be scheduled together in the same room)*")
    
    col_pg1, col_pg2 = st.columns(2)
    with col_pg1:
        core_options = ["None"] + pg_course_names
        idx = core_options.index(default_pg_core) if default_pg_core in core_options else 0
        shared_core = st.selectbox("Shared Core Course", options=core_options, index=idx)
    
    with col_pg2:
        st.info("ℹ️ **Professional Electives & Labs**\n\nThe solver engine will automatically sync ALL Professional Electives and Labs to happen concurrently for SP-1 and SP-2. No manual selection required!")

st.divider()

st.header("3. Manual Slot Overrides (Maths)")
st.markdown("""
Since Maths faculty belong to a different department, their workload is not tracked here. 
Use the grid below to **lock exactly when and where Maths happens**. 
Add a row for each Maths slot needed across your classes.
""")

all_classes = [
    "3A", "3B", "3C", "3D", "4A", "4B", "4C", "4D"
]
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
slots = [
    "S1 (9:00 - 9:55)", "S2 (9:55 - 10:50)", "S3 (11:05 - 12:00)", 
    "S4 (12:00 - 12:50)", "S5 (1:45 - 2:40)", "S6 (2:40 - 3:35)", "S7 (3:35 - 4:30)"
]

default_maths = current_config.get("maths_slots", [])
if not default_maths:
    default_maths = [
        # 3A
        {"Class": "3A", "Day": "Monday", "Slot": "S2 (9:55 - 10:50)", "Faculty": "MATHS"},
        {"Class": "3A", "Day": "Wednesday", "Slot": "S3 (11:05 - 12:00)", "Faculty": "MATHS"},
        {"Class": "3A", "Day": "Thursday", "Slot": "S4 (12:00 - 12:50)", "Faculty": "MATHS"},
        {"Class": "3A", "Day": "Tuesday", "Slot": "S1 (9:00 - 9:55)", "Faculty": "MATHS TUT"},
        # 3B
        {"Class": "3B", "Day": "Monday", "Slot": "S4 (12:00 - 12:50)", "Faculty": "MATHS"},
        {"Class": "3B", "Day": "Tuesday", "Slot": "S2 (9:55 - 10:50)", "Faculty": "MATHS TUT"},
        {"Class": "3B", "Day": "Wednesday", "Slot": "S3 (11:05 - 12:00)", "Faculty": "MATHS"},
        {"Class": "3B", "Day": "Thursday", "Slot": "S1 (9:00 - 9:55)", "Faculty": "MATHS"},
        # 3C
        {"Class": "3C", "Day": "Monday", "Slot": "S4 (12:00 - 12:50)", "Faculty": "MATHS"},
        {"Class": "3C", "Day": "Wednesday", "Slot": "S5 (1:45 - 2:40)", "Faculty": "MATHS TUT"},
        {"Class": "3C", "Day": "Thursday", "Slot": "S3 (11:05 - 12:00)", "Faculty": "MATHS"},
        {"Class": "3C", "Day": "Friday", "Slot": "S2 (9:55 - 10:50)", "Faculty": "MATHS"},
        # 3D
        {"Class": "3D", "Day": "Monday", "Slot": "S4 (12:00 - 12:50)", "Faculty": "MATHS"},
        {"Class": "3D", "Day": "Tuesday", "Slot": "S3 (11:05 - 12:00)", "Faculty": "MATHS"},
        {"Class": "3D", "Day": "Wednesday", "Slot": "S3 (11:05 - 12:00)", "Faculty": "MATHS TUT"},
        {"Class": "3D", "Day": "Friday", "Slot": "S1 (9:00 - 9:55)", "Faculty": "MATHS"},
    ]

df_maths = pd.DataFrame(default_maths)
edited_maths_df = st.data_editor(
    df_maths,
    num_rows="dynamic",
    column_config={
        "Class": st.column_config.SelectboxColumn("Class Section", options=all_classes, required=True),
        "Day": st.column_config.SelectboxColumn("Day", options=days, required=True),
        "Slot": st.column_config.SelectboxColumn("Slot", options=slots, required=True),
        "Faculty": st.column_config.TextColumn("Faculty Name (Optional)", default="Maths Faculty")
    },
    use_container_width=True
)

st.divider()

# =====================================================================
# NEW: CSE Lab Allocation for 1st & 2nd Semester
# =====================================================================
st.header("4. CSE Lab Allocation (1st & 2nd Semester)")
st.markdown("""
Assign **CSE Labs 1–4** to specific class sections (1A,1B,1C,2A,2B,2C) on fixed days and time slots.
The lab subject itself is irrelevant – this reserves the **room** for that section at that time.
Add a row for each required lab session.
""")

# Define available lab rooms and sections
lab_rooms = ["CSE Lab 1", "CSE Lab 2", "CSE Lab 3", "CSE Lab 4"]
first_second_sections = ["1A", "1B", "1C", "1K", "2A", "2B", "2C", "2K"]
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
slots = [
    "S1 (9:00 - 9:55)", "S2 (9:55 - 10:50)", "S3 (11:05 - 12:00)", 
    "S4 (12:00 - 12:50)", "S5 (1:45 - 2:40)", "S6 (2:40 - 3:35)", "S7 (3:35 - 4:30)"
]

# Load existing lab allocations if any; start with a single empty row as example
default_lab_alloc = current_config.get("cse_lab_allocations", [])
if not default_lab_alloc:
    # Provide one empty row as a template (like Maths table)
    default_lab_alloc = [{"Class": "", "Lab Room": "", "Day": "", "Slot": ""}]

df_lab = pd.DataFrame(default_lab_alloc)

edited_lab_df = st.data_editor(
    df_lab,
    num_rows="dynamic",   # allow adding/removing rows
    column_config={
        "Class": st.column_config.SelectboxColumn("Class Section", options=first_second_sections, required=True),
        "Lab Room": st.column_config.SelectboxColumn("Lab Room", options=lab_rooms, required=True),
        "Day": st.column_config.SelectboxColumn("Day", options=days, required=True),
        "Slot": st.column_config.SelectboxColumn("Slot", options=slots, required=True),
    },
    use_container_width=True,
    key="lab_alloc_editor"
)

st.divider()

# ----------------------------------------------------------------------
# Save all constraints
# ----------------------------------------------------------------------
if st.button("💾 Save Constraints", type="primary"):
    # Convert dataframes to list of dicts
    math_slots_list = edited_maths_df.to_dict(orient="records")
    lab_alloc_list = edited_lab_df.to_dict(orient="records")
    
    doc = {
        "type": "special_subjects",
        "open_electives": selected_oes,
        "aec": selected_aec,
        "pg_shared_core": shared_core if shared_core != "None" else None,
        "maths_slots": math_slots_list,
        "cse_lab_allocations": lab_alloc_list
    }
    
    try:
        constraints_col = db["constraints"]
        constraints_col.update_one({"type": "special_subjects"}, {"$set": doc}, upsert=True)
        st.success("✅ Constraint mappings updated successfully in the database!")
    except Exception as e:
        st.warning(f"⚠️ Database offline or error: {e}. Constraints were not saved, but the UI is functioning!", icon="⚠️")

if st.button("🗑️ Delete Constraints"):
    try:
        constraints_col = db["constraints"]
        result = constraints_col.delete_one({"type": "special_subjects"})
        if result.deleted_count:
            st.success("✅ Constraints deleted.")
        else:
            st.info("No saved constraints to delete.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ Failed to delete: {e}")