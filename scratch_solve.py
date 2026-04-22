"""Full end-to-end solver test."""
from engine.solver import build_and_solve

result = build_and_solve(semester="odd", time_limit_seconds=60)
print("STATUS:", result.get("status"))
print("ERRORS:", result.get("errors"))
print("STATS:", result.get("stats"))
tt = result.get("timetables", {})
print("SECTIONS:", sorted(tt.keys()) if tt else "NONE")
if tt:
    for sec in sorted(tt.keys()):
        grid = tt[sec]
        filled = sum(1 for row in grid for cell in row if cell)
        print(f"  {sec}: {filled} filled slots")
