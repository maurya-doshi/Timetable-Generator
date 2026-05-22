import streamlit as st

st.set_page_config(
    page_title="Timetable Generator",
    page_icon="📅",
    layout="wide",
)

st.title("📅 Timetable Generator")
st.markdown(
    """
    Welcome to the **Timetable Generator**! Use the sidebar to navigate:

    1. **Input Data** — Upload faculty & subject allocations via Excel
    2. **Constraints** — Configure OE, AEC, PG shared classes, maths locks & lab allocations
    3. **Generate** — Run the CP-SAT solver and export timetables as PDF
    """
)
