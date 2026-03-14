import streamlit as st
from openpyxl import load_workbook
from io import BytesIO
from collections import defaultdict
from db import get_db

st.set_page_config(page_title="Input Data", page_icon="📋", layout="wide")

st.title("📋 Input Data — Faculty & Subject Allocation")

st.markdown(
    """
    Upload an **Excel file** (`.xlsx`) containing faculty-subject allotment
    for a semester (Odd or Even).

    ### Expected Format

    The Excel file should have the following structure:

    | Sl. No. | Name of Faculty | Designation | *ODD/EVEN SEMESTER (merged)* | | | | |
    |---|---|---|---|---|---|---|---|
    | | | | Subject 1 | Subject 2 | Subject 3 | Lab 1 | Lab 2 |
    | 1 | Dr. Sharma | Professor | Data Structures | Algorithms | | Networks Lab | |
    | 2 | Prof. Mehta | Assoc Prof | DBMS | OS | Compilers | | |

    > Decorative rows (logos, title, etc.) above the header are automatically skipped.
    """
)

st.divider()

# ---------------------------------------------------------------------------
# Semester selection
# ---------------------------------------------------------------------------
semester = st.radio("Which semester is this file for?", ["Odd", "Even"], horizontal=True)

# ---------------------------------------------------------------------------
# Excel parser
# ---------------------------------------------------------------------------

def find_header_row(ws):
    """Scan rows to find the one containing 'Sl' or 'SI' in any cell
    (case-insensitive). Returns the 1-based row number, or None."""
    for row_idx in range(1, min(ws.max_row + 1, 100)):
        for cell in ws[row_idx]:
            val = str(cell.value).strip().lower() if cell.value else ""
            if "sl" in val and "no" in val:
                return row_idx
            if "si" in val and "no" in val:
                return row_idx
            if val in ("sl. no.", "si. no.", "sl.no.", "si.no.", "s.no.",
                       "sl. no", "si. no", "sl", "si", "s.no"):
                return row_idx
    return None


def parse_faculty_excel(file_bytes):
    """Parse the uploaded Excel file and return (records, debug_info, error).

    Each record is a dict:
      { "sl_no": int, "name": str, "designation": str,
        "subjects": [str, ...], "labs": [str, ...] }
    """
    debug = {}

    try:
        # NOTE: read_only=False is required to correctly read merged cells
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
            "Could not find the header row (looking for a cell containing "
            "'Sl. No.' or similar). Please check the file format."
        )

    debug["header_row"] = header_row

    # Read what's in the header row for debugging
    header_cells = []
    for cell in ws[header_row]:
        header_cells.append(f"Col{cell.column}: {repr(cell.value)}")
    debug["header_cells"] = header_cells

    # Scan rows around the header to find the Subject and Lab columns
    # In some merged formats, they are *above* or *in* the Sl.No row, 
    # instead of strictly below. We'll scan rows 1 to header_row + 2.
    subject_cols = []
    lab_cols = []
    
    # The actual "Subject 1", "Lab 1" headers might be on row 57 or elsewhere.
    # We scan all rows from 1 to 100 to confidently locate those columns.
    scan_end = min(ws.max_row, 100)
    for r in range(1, scan_end + 1):
        for cell in ws[r]:
            col_idx = cell.column - 1
            # Skip the first 3 columns (Sl.No, Name, Designation)
            if col_idx < 3:
                continue
                
            val = str(cell.value).strip().lower() if cell.value else ""
            if "subject" in val and col_idx not in subject_cols:
                subject_cols.append(col_idx)
            elif "lab" in val and col_idx not in lab_cols:
                lab_cols.append(col_idx)

    debug["subject_cols"] = subject_cols
    debug["lab_cols"] = lab_cols

    if not subject_cols and not lab_cols:
        return None, debug, (
            f"Could not find Subject / Lab columns anywhere in rows 1 to {scan_end}. "
            "Expected cells containing the word 'Subject' or 'Lab'."
        )

    # Data rows start immediately after the header row (from the debug info provided, 
    # row 11 is the first data row, right after the header on row 10).
    data_start = header_row + 1
    records = []

    for row in ws.iter_rows(min_row=data_start, values_only=True):
        # Skip completely empty rows
        if not any(cell for cell in row):
            continue

        # Column indices: 0 = Sl.No., 1 = Name, 2 = Designation
        # Only process rows where we have a valid Sl. No (usually an integer) and Name
        sl_no_raw = row[0] if len(row) > 0 else None
        name_raw = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        designation = str(row[2]).strip() if len(row) > 2 and row[2] else ""

        # A valid faculty row must have a non-empty name, and typically a numeric/valid Sl. No.
        if not name_raw or name_raw.lower() in ("none", "", "name of faculty"):
            continue

        # Collect subjects and labs (skip empty cells)
        subjects = []
        for col_idx in subject_cols:
            if col_idx < len(row) and row[col_idx]:
                val = str(row[col_idx]).strip()
                if val and val.lower() != "none" and val != name_raw:
                    subjects.append(val)

        labs = []
        for col_idx in lab_cols:
            if col_idx < len(row) and row[col_idx]:
                val = str(row[col_idx]).strip()
                if val and val.lower() != "none" and val != name_raw:
                    labs.append(val)

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
    "Upload Faculty-Subject Excel File",
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
    header_cols[3].markdown("**Subjects**")
    header_cols[4].markdown("**Labs**")

    for rec in records:
        cols = st.columns([0.5, 2, 1.5, 3, 2])
        cols[0].write(rec["sl_no"] if rec["sl_no"] else "—")
        cols[1].write(rec["name"])
        cols[2].write(rec["designation"])
        cols[3].write(", ".join(rec["subjects"]) if rec["subjects"] else "—")
        cols[4].write(", ".join(rec["labs"]) if rec["labs"] else "—")

    # --- Summary ----------------------------------------------------------
    st.subheader("Summary")
    all_subjects = set()
    all_labs = set()
    for rec in records:
        all_subjects.update(rec["subjects"])
        all_labs.update(rec["labs"])

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
                "semester": semester.lower(),
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
            subj_str = ", ".join(doc.get("subjects", []))
            lab_str = ", ".join(doc.get("labs", []))
            st.markdown(
                f"- **{doc['name']}** ({doc.get('designation', '')}) — "
                f"Subjects: {subj_str or '—'} | Labs: {lab_str or '—'}"
            )
    else:
        st.info(
            f"No faculty records for **{semester}** semester yet. "
            "Upload a file and click **Save to Database**."
        )
