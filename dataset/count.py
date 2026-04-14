#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///

import json
from pathlib import Path

BASE = Path(__file__).parent
DIRS = ["local-test", "instrument-test"]


def count_smells(file: Path) -> int:
    data = json.loads(file.read_text(encoding="utf-8"))
    # data-clumps format: array of smell objects (no 'messages' key)
    if data and "messages" not in data[0]:
        return len(data)
    # regular format: array of {filePath, messages: [...]}
    return sum(len(entry["messages"]) for entry in data)


lines: list[str] = []
total = 0
for dir_name in DIRS:
    dir_path = BASE / dir_name
    lines.append(dir_name)
    dir_total = 0
    for json_file in sorted(dir_path.glob("*.json")):
        count = count_smells(json_file)
        lines.append(f"{json_file.stem}: {count}")
        dir_total += count
    lines.append(f"total: {dir_total}")
    lines.append("")
    total += dir_total

lines.append(f"total: {total}")

output = "\n".join(lines)
print(output)
(BASE / "count.txt").write_text(output, encoding="utf-8")
