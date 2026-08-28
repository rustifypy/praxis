import csv

rows = [("region", "orders"), ("EU", 412), ("US", 733), ("APAC", 190)]
with open("orders.csv", "w", newline="") as handle:
    csv.writer(handle).writerows(rows)

# Never declared by phase.yml, so this must land in scratch/ rather than output/.
with open("notes.txt", "w") as handle:
    handle.write("a file nobody declared\n")

print("wrote", len(rows) - 1, "rows")
