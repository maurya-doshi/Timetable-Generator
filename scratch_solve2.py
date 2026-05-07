from engine.solver import _load_data, _build_mappings, _create_variables, _extract_solution
from ortools.sat.python import cp_model
from engine.constraints import *

data = _load_data("odd")
course_info = data["course_info"]
mappings = _build_mappings(course_info, data["faculty_raw"], data["constraints_doc"])
section_courses = mappings["section_courses"]
faculty_assignments = mappings["faculty_assignments"]

model = cp_model.CpModel()
x1, x2, co_fac = _create_variables(model, section_courses, course_info, mappings["faculty_designations"])

add_no_faculty_clash(model, x1, x2, co_fac, faculty_assignments, mappings["pg_core_code"], mappings["pg_sections"])
add_co_faculty_logic(model, x2, co_fac, faculty_assignments)
add_max_workload(model, x1, x2, co_fac, faculty_assignments, mappings["faculty_designations"], "odd")
add_no_section_clash(model, x1, x2, section_courses)
add_weekly_hours(model, x1, x2, section_courses, course_info)
add_no_student_gaps(model, x1, x2, section_courses)
add_faculty_break(model, x1, x2, faculty_assignments)

_blocked = add_cse_lab_locks(model, x1, x2, mappings["lab_alloc"])
lab_room = add_lab_room_assignment(model, x1, x2, section_courses, course_info, mappings["pg_sections"], _blocked)
add_friday_half_day(model, x1, x2, section_courses)
add_morning_first(model, x1, x2, section_courses)
add_no_empty_days(model, x1, x2, section_courses)

if mappings["oe_codes"]: add_oe_concurrency(model, x1, section_courses, mappings["oe_codes"])
if mappings["aec_codes"]: add_aec_concurrency(model, x1, section_courses, mappings["aec_codes"], mappings["sections_3rd"], mappings["sections_4th"])
if mappings["pg_sections"]: add_pg_shared(model, x1, x2, section_courses, mappings["pg_sections"], mappings["pg_core_code"], mappings["pg_pe_codes"])
if mappings["maths_slots"]: add_maths_locks(model, x1, x2, mappings["maths_slots"])

spread_pens = add_spread_penalty(model, x1, x2, section_courses)
morning_pens = add_faculty_morning_penalty(model, x1, x2, faculty_assignments)

co_fac_mismatches = []
fac_taught_courses = {}
for fac, assigns in faculty_assignments.items():
    fac_taught_courses[fac] = {cc for (_, cc) in assigns}
for (fac_name, sec, cc, d, t), var in co_fac.items():
    taught = fac_taught_courses.get(fac_name, set())
    if cc not in taught:
        co_fac_mismatches.append(var)

model.Minimize(sum(spread_pens) + sum(morning_pens) + 10 * sum(co_fac_mismatches))

solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 60
solver.parameters.num_workers = 8
solver.Solve(model)

print("OBJ:", solver.ObjectiveValue())
print("BEST BOUND:", solver.BestObjectiveBound())

spread_val = sum(solver.Value(p) for p in spread_pens)
morning_val = sum(solver.Value(p) for p in morning_pens)
mismatch_val = sum(solver.Value(p) for p in co_fac_mismatches)

print("SPREAD PENS:", spread_val)
print("MORNING PENS:", morning_val)
print("MISMATCH PENS:", mismatch_val)

for fac, assignments in faculty_assignments.items():
    morning_vars = []
    for sec, cc in assignments:
        for d in range(NUM_DAYS):
            for t in MORNING_SLOTS:
                k1 = (sec, cc, d, t)
                if k1 in x1: morning_vars.append(x1[k1])
            for t in [0, 2]:
                for etype in ("T", "P"):
                    k2 = (sec, cc, etype, d, t)
                    if k2 in x2: morning_vars.append(x2[k2])
    fac_has_morning = any(solver.Value(v) > 0 for v in morning_vars)
    if not fac_has_morning:
        print(f"FACULTY WITH NO MORNING: {fac} (Assigns: {assignments})")
