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
    2. **Constraints** — Define scheduling rules *(coming soon)*
    3. **Generate** — Run the solver *(coming soon)*
    4. **Export** — Download your timetable *(coming soon)*
    """
)
