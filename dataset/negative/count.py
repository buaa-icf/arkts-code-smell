#!/usr/bin/env python3

import json
from pathlib import Path

BASE = Path(__file__).parent


def count_smells(file: Path) -> int:
    data = json.loads(file.read_text(encoding="utf-8"))
    if data and isinstance(data[0], dict) and "messages" in data[0]:
        return sum(len(entry.get("messages", [])) for entry in data)
    return len(data)


lines: list[str] = []
total = 0
for json_file in sorted(BASE.glob("*.json")):
    if json_file.name == "To-do.json":
        continue
    count = count_smells(json_file)
    lines.append(f"{json_file.stem}: {count}")
    total += count

lines.append("")
lines.append(f"total: {total}")

output = "\n".join(lines)
print(output)
(BASE / "count.txt").write_text(output, encoding="utf-8")
