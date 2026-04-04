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

if not course_names:
    st.info("No courses found in the `courses` collection. Once the Master Subject List parsing is complete, they will appear here.", icon="🕒")

# --- Fetch existing configuration ---
current_config = {}
try:
    constraints_col = db["constraints"]
    current_config = constraints_col.find_one({"type": "special_subjects"}) or {}
except Exception:
    pass

# Handle defaults
default_oe = current_config.get("open_electives", [])
default_oe = [x for x in default_oe if x in course_names]

default_aec = current_config.get("aec", [])
default_aec = [x for x in default_aec if x in course_names]

default_pg_core = current_config.get("pg_shared_core", "None")
if default_pg_core not in course_names:
    default_pg_core = "None"

default_pg_pe = current_config.get("pg_shared_pe", [])
default_pg_pe = [x for x in default_pg_pe if x in course_names]

st.header("1. Special Subject Identifiers")
st.markdown("Assign specific behaviors to subjects (e.g., Open Electives must run Monday 5th slot).")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Open Electives (OE)")
    st.markdown("OE for 5th/6th/7th sem are scheduled concurrently on **Monday, 5th Slot**.")
    selected_oes = st.multiselect(
        "Select Open Elective Subjects", 
        options=course_names, 
        default=default_oe,
        key="oe"
    )

with col2:
    st.subheader("Ability Enhancement Course (AEC)")
    st.markdown("AEC for **3rd & 4th sem** is scheduled at the **same time** for all sections.")
    selected_aec = st.multiselect(
        "Select AEC Subjects", 
        options=course_names, 
        default=default_aec,
        key="aec"
    )

st.divider()

st.header("2. PG Shared Classes (SP-1 & SP-2)")
st.markdown("Select subjects that are shared between PG Specialization 1 and Specialization 2 and should be conducted together in the same room.")

col3, col4 = st.columns(2)

with col3:
    st.subheader("Shared Core Course")
    core_options = ["None"] + course_names
    core_index = core_options.index(default_pg_core) if default_pg_core in core_options else 0
    shared_core = st.selectbox(
        "Select Core Course", 
        options=core_options, 
        index=core_index,
        key="pg_core"
    )

with col4:
    st.subheader("Shared Professional Electives")
    st.markdown("Select exactly **2** Shared Electives.")
    shared_pe = st.multiselect(
        "Select Shared Electives", 
        options=course_names, 
        default=default_pg_pe,
        key="pg_pe"
    )

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
    default_maths = [{"Class": "", "Day": "", "Slot": "", "Faculty": ""}]

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
first_second_sections = ["1A", "1B", "1C", "2A", "2B", "2C"]
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
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
        "pg_shared_pe": shared_pe,
        "maths_slots": math_slots_list,
        "cse_lab_allocations": lab_alloc_list   # new field
    }
    
    try:
        constraints_col = db["constraints"]
        constraints_col.update_one({"type": "special_subjects"}, {"$set": doc}, upsert=True)
        st.success("✅ Constraint mappings updated successfully in the database!")
    except Exception as e:
        st.warning(f"⚠️ Database offline or error: {e}. Constraints were not saved, but the UI is functioning!", icon="⚠️")