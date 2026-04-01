import streamlit as st
from openpyxl import load_workbook
from io import BytesIO
from collections import defaultdict
from db import get_db

st.set_page_config(page_title="Input Data", page_icon="📋", layout="wide")

st.title("📋 Input Data — Faculty & Subject Allocation")

st.markdown(
    """
    Upload an **Excel file** (`.xlsx`) containing faculty‑subject allotment.
    The file must follow the format below:

    | Sr No. | Name | Designation | Subject |           |           | Lab |       |
    |--------|------|-------------|---------|-----------|-----------|-----|-------|
    |        |      |             | S1      | Sem of S1 | S2        | L1  | Sem L1|
    | 1      | ...  | ...         | CS301   | 3         | ...       | ... | ...   |

    > The two header rows (Sr No., Name, … and S1, Sem of S1, …) are required.
      Decorative rows above them are automatically skipped.
    """
)

st.divider()

# ---------------------------------------------------------------------------
# Semester selection
# ---------------------------------------------------------------------------
semester = st.radio("Which semester is this file for?", ["Odd", "Even"], horizontal=True)

# ---------------------------------------------------------------------------
# Excel parser – adapted for the two‑row header format
# ---------------------------------------------------------------------------

def find_header_row(ws):
    """
    Scan rows to find the one containing 'Sr No.', 'Name', 'Designation', etc.
    Returns the 1‑based row number, or None.
    """
    for row_idx in range(1, min(ws.max_row + 1, 100)):
        row_values = [str(cell.value).strip().lower() if cell.value else "" for cell in ws[row_idx]]
        # Look for a row that has a cell with "sr" and "no" (for Sr No.)
        has_sr = any(("sr" in v and "no" in v) for v in row_values)
        # Also look for "name" and "designation" in the same row
        has_name = any("name" in v for v in row_values)
        has_design = any("designation" in v or "design" in v for v in row_values)
        if has_sr and has_name and has_design:
            return row_idx
    return None

def parse_faculty_excel(file_bytes):
    """Parse the uploaded Excel file and return (records, debug_info, error).

    Each record is a dict:
      {
        "sl_no": int,
        "name": str,
        "designation": str,
        "subjects": [{"code": str, "semester": str}, ...],
        "labs": [{"code": str, "semester": str}, ...]
      }
    """
    debug = {}

    try:
        wb = load_workbook(filename=BytesIO(file_bytes), data_only=True)
        ws = wb.active
    except Exception as e:
        return None, debug, f"Failed to read the Excel file: {e}"

    debug["sheet_name"] = ws.title
    debug["total_rows"] = ws.max_row
    debug["total_cols"] = ws.max_column

    header_row = find_header_row(ws)
    if header_row is None:
        return None, debug, (
            "Could not find the header row (looking for a row containing "
            "'Sr No.', 'Name', and 'Designation'). Please check the file format."
        )

    debug["header_row"] = header_row

    # The second header row is directly below the main header
    sub_header_row = header_row + 1
    debug["sub_header_row"] = sub_header_row

    # Read the sub-header row to identify subject and lab columns
    sub_header = [cell.value for cell in ws[sub_header_row]]

    subject_cols = []      # list of (subject_col_index, semester_col_index)
    lab_cols = []          # list of (lab_col_index, semester_col_index)

    # We'll skip the first three columns (Sr No., Name, Designation)
    col_idx = 3  # 0‑based index of column D (since columns A,B,C are fixed)
    while col_idx < len(sub_header):
        cell_val = str(sub_header[col_idx]).strip() if sub_header[col_idx] else ""
        # Look for a subject slot (e.g., "S1", "S2", ...)
        if cell_val.startswith("S") and not cell_val.startswith("Sem"):
            # Next column should be the semester column
            if col_idx + 1 < len(sub_header):
                semester_val = str(sub_header[col_idx + 1]).strip() if sub_header[col_idx + 1] else ""
                if "Sem" in semester_val:
                    subject_cols.append((col_idx, col_idx + 1))
                    col_idx += 2
                    continue
        # Look for a lab slot (e.g., "L1", "L2", ...)
        elif cell_val.startswith("L"):
            if col_idx + 1 < len(sub_header):
                semester_val = str(sub_header[col_idx + 1]).strip() if sub_header[col_idx + 1] else ""
                if "Sem" in semester_val:
                    lab_cols.append((col_idx, col_idx + 1))
                    col_idx += 2
                    continue
        # If neither, just move to next column
        col_idx += 1

    debug["subject_cols"] = subject_cols
    debug["lab_cols"] = lab_cols

    if not subject_cols and not lab_cols:
        return None, debug, (
            "Could not find any Subject or Lab columns in the second header row. "
            "Expected cells like 'S1', 'Sem of S1', 'L1', 'Sem of L1'."
        )

    # Data rows start right after the sub-header row
    data_start = sub_header_row + 1
    records = []

    for row in ws.iter_rows(min_row=data_start, values_only=True):
        # Skip completely empty rows
        if not any(cell for cell in row):
            continue

        # Column indices: 0 = Sr No., 1 = Name, 2 = Designation
        sl_no_raw = row[0] if len(row) > 0 else None
        name_raw = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        designation = str(row[2]).strip() if len(row) > 2 and row[2] else ""

        # A valid faculty row must have a non‑empty name
        if not name_raw or name_raw.lower() in ("none", "", "name of faculty"):
            continue

        subjects = []
        for subj_col, sem_col in subject_cols:
            if subj_col < len(row) and row[subj_col]:
                subj_code = str(row[subj_col]).strip()
                semester_val = str(row[sem_col]).strip() if sem_col < len(row) and row[sem_col] else ""
                if subj_code and subj_code.lower() != "none" and subj_code != name_raw:
                    subjects.append({"code": subj_code, "semester": semester_val})

        labs = []
        for lab_col, sem_col in lab_cols:
            if lab_col < len(row) and row[lab_col]:
                lab_code = str(row[lab_col]).strip()
                semester_val = str(row[sem_col]).strip() if sem_col < len(row) and row[sem_col] else ""
                if lab_code and lab_code.lower() != "none" and lab_code != name_raw:
                    labs.append({"code": lab_code, "semester": semester_val})

        records.append({
            "sl_no": sl_no_raw,
            "name": name_raw,
            "designation": designation,
            "subjects": subjects,
            "labs": labs,
        })

    wb.close()

    if not records:
        return None, debug, "No faculty data rows found after the header. Is the file empty?"

    return records, debug, None


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Upload Faculty‑Subject Excel File",
    type=["xlsx"],
    help="Upload a .xlsx file with the format described above.",
)

if uploaded_file is not None:
    records, debug, error = parse_faculty_excel(uploaded_file.read())

    if error:
        st.error(error)
        with st.expander("🛠️ Parser Debug Info (Expand to view why it failed)"):
            st.json(debug)
        st.stop()

    st.success(f"✅ Parsed **{len(records)} faculty record(s)** from the file.")

    # --- Preview table ----------------------------------------------------
    st.subheader("Preview")

    # Build a table-like display
    header_cols = st.columns([0.5, 2, 1.5, 3, 2])
    header_cols[0].markdown("**Sl.**")
    header_cols[1].markdown("**Faculty Name**")
    header_cols[2].markdown("**Designation**")
    header_cols[3].markdown("**Subjects (Semester)**")
    header_cols[4].markdown("**Labs (Semester)**")

    for rec in records:
        cols = st.columns([0.5, 2, 1.5, 3, 2])
        cols[0].write(rec["sl_no"] if rec["sl_no"] else "—")
        cols[1].write(rec["name"])
        cols[2].write(rec["designation"])
        subj_str = ", ".join(f"{s['code']} ({s['semester']})" for s in rec["subjects"]) if rec["subjects"] else "—"
        lab_str = ", ".join(f"{l['code']} ({l['semester']})" for l in rec["labs"]) if rec["labs"] else "—"
        cols[3].write(subj_str)
        cols[4].write(lab_str)

    # --- Summary ----------------------------------------------------------
    st.subheader("Summary")
    all_subjects = set()
    all_labs = set()
    for rec in records:
        for s in rec["subjects"]:
            all_subjects.add((s["code"], s["semester"]))
        for l in rec["labs"]:
            all_labs.add((l["code"], l["semester"]))

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Faculty", len(records))
    with col2:
        st.metric("Unique Subjects", len(all_subjects))
    with col3:
        st.metric("Unique Labs", len(all_labs))

    # --- Save to MongoDB --------------------------------------------------
    st.divider()
    if st.button("💾 Save to Database", type="primary"):
        db = get_db()
        collection_name = f"faculty_{semester.lower()}"
        col = db[collection_name]

        docs = []
        for rec in records:
            docs.append({
                "sl_no": rec["sl_no"],
                "name": rec["name"],
                "designation": rec["designation"],
                "subjects": rec["subjects"],
                "labs": rec["labs"],
                "semester": semester.lower(),        # overall semester (odd/even)
            })

        # Replace existing data for this semester
        col.delete_many({})
        if docs:
            col.insert_many(docs)

        st.success(
            f"✅ Saved **{len(docs)} faculty member(s)** to `{collection_name}` collection!"
        )

    # --- Current DB records -----------------------------------------------
    st.divider()
    st.subheader("Current Database Records")
    db = get_db()
    collection_name = f"faculty_{semester.lower()}"
    existing = list(db[collection_name].find({}, {"_id": 0}))
    if existing:
        for doc in existing:
            subj_str = ", ".join(f"{s['code']} ({s['semester']})" for s in doc.get("subjects", []))
            lab_str = ", ".join(f"{l['code']} ({l['semester']})" for l in doc.get("labs", []))
            st.markdown(
                f"- **{doc['name']}** ({doc.get('designation', '')}) — "
                f"Subjects: {subj_str or '—'} | Labs: {lab_str or '—'}"
            )
    else:
        st.info(
            f"No faculty records for **{semester}** semester yet. "
            "Upload a file and click **Save to Database**."
        )