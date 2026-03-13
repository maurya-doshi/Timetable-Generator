import streamlit as st
from openpyxl import load_workbook
from io import BytesIO
from collections import defaultdict
from db import get_db

st.set_page_config(page_title="Input Data", page_icon="📋", layout="wide")

st.title("📋 Input Data — Faculty & Subject Allocation")

st.markdown(
    """
    Upload an **Excel file** (`.xlsx`) containing faculty names and their allocated subjects.

    ### Expected Format

    | Faculty Name | Subject |
    |---|---|
    | Dr. Sharma | Data Structures |
    | Dr. Sharma | Algorithms |
    | Prof. Mehta | Database Systems |
    | Prof. Mehta | Operating Systems |
    | Dr. Patel | Computer Networks |

    - Each row maps **one faculty** to **one subject**.
    - A faculty member can appear in multiple rows if they teach multiple subjects.
    """
)

st.divider()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_excel(file_bytes):
    """Parse the uploaded Excel file using openpyxl.
    Returns (rows, error) where rows is a list of (faculty, subject) tuples.
    """
    try:
        wb = load_workbook(filename=BytesIO(file_bytes), read_only=True)
        ws = wb.active
    except Exception as e:
        return None, f"Failed to read the Excel file: {e}"

    headers = [str(cell.value).strip() if cell.value else "" for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    # Find required columns (case-insensitive)
    header_lower = [h.lower() for h in headers]
    faculty_idx = None
    subject_idx = None
    for i, h in enumerate(header_lower):
        if "faculty" in h:
            faculty_idx = i
        if "subject" in h:
            subject_idx = i

    if faculty_idx is None or subject_idx is None:
        return None, (
            f"Missing required column(s). Need **Faculty Name** and **Subject**. "
            f"Found columns: {', '.join(headers)}"
        )

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        faculty = str(row[faculty_idx]).strip() if row[faculty_idx] else ""
        subject = str(row[subject_idx]).strip() if row[subject_idx] else ""
        if faculty and subject:
            rows.append((faculty, subject))

    wb.close()
    return rows, None


# ---------------------------------------------------------------------------
# Excel Upload
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Upload Faculty-Subject Excel File",
    type=["xlsx"],
    help="Upload a .xlsx file with columns: Faculty Name, Subject",
)

if uploaded_file is not None:
    rows, error = parse_excel(uploaded_file.read())

    if error:
        st.error(error)
        st.stop()

    st.success(f"✅ File read successfully — **{len(rows)} record(s)** found.")

    # --- Preview ----------------------------------------------------------
    st.subheader("Preview")

    preview_cols = st.columns([1, 1])
    with preview_cols[0]:
        st.markdown("**Faculty Name**")
    with preview_cols[1]:
        st.markdown("**Subject**")

    for faculty, subject in rows:
        with preview_cols[0]:
            st.text(faculty)
        with preview_cols[1]:
            st.text(subject)

    # --- Summary ----------------------------------------------------------
    st.subheader("Summary")

    # Group subjects by faculty
    faculty_map = defaultdict(list)
    for faculty, subject in rows:
        if subject not in faculty_map[faculty]:
            faculty_map[faculty].append(subject)

    all_subjects = set()
    for faculty, subject in rows:
        all_subjects.add(subject)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Faculty", len(faculty_map))
    with col2:
        st.metric("Total Subjects", len(all_subjects))

    st.markdown("#### Faculty → Subjects Mapping")
    for faculty, subjects in sorted(faculty_map.items()):
        st.markdown(f"- **{faculty}**: {', '.join(subjects)}")

    # --- Save to MongoDB --------------------------------------------------
    st.divider()
    if st.button("💾 Save to Database", type="primary"):
        db = get_db()
        faculty_col = db["faculty"]

        docs = [
            {"name": faculty, "subjects": subjects}
            for faculty, subjects in sorted(faculty_map.items())
        ]

        # Replace existing data (fresh import each time)
        faculty_col.delete_many({})
        if docs:
            faculty_col.insert_many(docs)

        st.success(f"✅ Saved **{len(docs)} faculty member(s)** to the database!")

    # --- Show current DB state --------------------------------------------
    st.divider()
    st.subheader("Current Database Records")
    db = get_db()
    existing = list(db["faculty"].find({}, {"_id": 0}))
    if existing:
        for doc in existing:
            st.markdown(f"- **{doc['name']}**: {', '.join(doc.get('subjects', []))}")
    else:
        st.info("No faculty records in the database yet. Upload a file and click **Save to Database**.")
