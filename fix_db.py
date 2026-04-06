with open("scripts/cortex/db.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_conflict = False
for line in lines:
    if line.startswith("<<<<<<< Updated upstream"):
        in_conflict = True
        continue
    elif line.startswith("======="):
        continue
    elif line.startswith(">>>>>>> Stashed changes"):
        in_conflict = False
        continue

    if in_conflict and ("c[1] for c in node_cols" in line or "node_cols =" in line or "cache_cols_info =" in line or "cache_columns = [c[1]" in line):
        new_lines.append(line)
    elif not in_conflict:
        new_lines.append(line)

with open("scripts/cortex/db.py", "w") as f:
    f.writelines(new_lines)
