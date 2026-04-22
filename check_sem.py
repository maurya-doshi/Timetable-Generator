from engine.solver import _load_data, _build_mappings
data = _load_data("odd")
ci = data["course_info"]
m = _build_mappings(ci, data["faculty_raw"], data["constraints_doc"])
print("OE codes:", m["oe_codes"])
for cc in m["oe_codes"]:
    if cc in ci:
        print(f"  {cc}: L={ci[cc]['L']}, T={ci[cc]['T']}, P={ci[cc]['P']}, sem={ci[cc]['semester']}")
# Which sections have OE courses?
for sec, courses in m["section_courses"].items():
    oe_in_sec = [cc for cc in courses if cc in m["oe_codes"]]
    if oe_in_sec:
        print(f"  Section {sec} has OE: {oe_in_sec}")
