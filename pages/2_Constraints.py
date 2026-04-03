import streamlit as st
import pandas as pd
from db import get_db

st.set_page_config(page_title="Constraints Builder", page_icon="⚙️", layout="wide")

st.title("⚙️ Constraints Builder")
st.markdown("Configure special scheduling rules and tag specific subjects to trigger hardcoded constraints.")

# Connect to database with a short timeout for the UI
db = get_db()
db.client.get_io_loop = db.client.get_io_loop if hasattr(db.client, 'get_io_loop') else None

try:
    # Quick ping to check if mongo is reachable
    db.client.admin.command('ping')
    courses_cursor = db["courses"].find({}, {"_id": 0})
    courses = list(courses_cursor)
except Exception:
    # Use dummy data if DB is offline for testing the UI
    courses = [
        {"course_code": "CS301", "course_name": "Data Structures"},
        {"course_code": "CS304", "course_name": "AEC - EVS"},
        {"course_code": "PG101", "course_name": "Advanced DBMS (Core)"},
        {"course_code": "OE101", "course_name": "Open Elective - AI Base"},
    ]

# Extract course names for selection
# We default to an empty list, ready for when the parser is done
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

# Handle defaults safely if they aren't loaded yet
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
    
    # Needs a combined list with "None"
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

# Define classes & slots for dropdowns
all_classes = [
    "3A", "3B", "3C", "3D", "5A", "5B", "5C", "5D", "7A", "7B", "7C", 
    "4A", "4B", "4C", "4D", "6A", "6B", "6C", "6D", "PG-SP1", "PG-SP2"
]
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
slots = [
    "S1 (9:00 - 9:55)", "S2 (9:55 - 10:50)", "S3 (11:05 - 12:00)", 
    "S4 (12:00 - 12:50)", "S5 (1:45 - 2:40)", "S6 (2:40 - 3:35)", "S7 (3:35 - 4:30)"
]

# Load existing Maths constraints if any
default_maths = current_config.get("maths_slots", [])
if not default_maths:
    default_maths = [{"Class": "3A", "Day": "Monday", "Slot": "S1 (9:00 - 9:55)", "Faculty": "Prof. X"}]

df_maths = pd.DataFrame(default_maths)

# Interactive data editor
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

if st.button("💾 Save Constraints", type="primary"):
    # Convert dataframe back to dicts
    math_slots_list = edited_maths_df.to_dict(orient="records")
    
    doc = {
        "type": "special_subjects",
        "open_electives": selected_oes,
        "aec": selected_aec,
        "pg_shared_core": shared_core if shared_core != "None" else None,
        "pg_shared_pe": shared_pe,
        "maths_slots": math_slots_list
    }
    
    try:
        constraints_col = db["constraints"]
        constraints_col.update_one({"type": "special_subjects"}, {"$set": doc}, upsert=True)
        st.success("✅ Constraint mappings updated successfully in the database!")
    except Exception:
        st.warning("⚠️ Database offline. Constraints were not saved, but the UI is functioning!", icon="⚠️")
