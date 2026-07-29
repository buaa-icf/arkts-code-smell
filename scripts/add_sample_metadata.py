import csv
import json
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT_ROOT = REPOSITORY_ROOT.parent
DATASET_ROOT = REPOSITORY_ROOT / "dataset"
POSITIVE_ROOT = DATASET_ROOT / "positive"


def normalized_path(value: str) -> str:
    return value.strip().replace("\\", "/")


def repository_name(source_file: str) -> str:
    path = normalized_path(source_file)
    supplement_prefix = normalized_path(str(SUPPLEMENT_ROOT)) + "/"
    if path.lower().startswith(supplement_prefix.lower()):
        path = path[len(supplement_prefix):]
    return path.split("/", 1)[0]


def repository_relative_path(path: str, project: str) -> str:
    normalized = normalized_path(path)
    supplement_prefix = normalized_path(str(SUPPLEMENT_ROOT)) + "/"
    if normalized.lower().startswith(supplement_prefix.lower()):
        normalized = normalized[len(supplement_prefix):]
    project_prefix = project + "/"
    return normalized[len(project_prefix):] if normalized.startswith(project_prefix) else ""


def latest_commit(project: str, paths: list[str]) -> str:
    repository = SUPPLEMENT_ROOT / project
    if not project or not (repository / ".git").exists():
        return ""

    relative_paths = []
    for path in paths:
        relative_path = repository_relative_path(path, project)
        if relative_path and relative_path not in relative_paths:
            relative_paths.append(relative_path)
    if not relative_paths:
        return ""

    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "-C",
            str(repository),
            "log",
            "-1",
            "--format=%H",
            "--",
            *relative_paths,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def metadata_fields(fields: list[str]) -> list[str]:
    result = [field for field in fields if field != "commit_hash"]
    insert_after = (
        "source_project"
        if "source_project" in result
        else "source_file"
    )
    insert_index = result.index(insert_after) + 1
    result.insert(insert_index, "commit_hash")
    return result


def update_coverage_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = metadata_fields(reader.fieldnames or [])

    for row in rows:
        source_file = row.get("source_file", "")
        project = repository_name(source_file)
        test_files = [
            test_file
            for test_file in row.get("test_files", "").split(";")
            if test_file
        ]
        row["commit_hash"] = latest_commit(project, [source_file, *test_files])

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def source_file_from_record(record: dict) -> str:
    return (
        record.get("filePath")
        or record.get("sourceFile")
        or record.get("path")
        or ""
    )


def add_json_commit_hash(record: dict, commit_hash: str) -> dict:
    updated = {}
    inserted = False
    for key, value in record.items():
        if key == "commitHash":
            continue
        updated[key] = value
        if key == "sourceProject":
            updated["commitHash"] = commit_hash
            inserted = True
    if not inserted:
        updated["commitHash"] = commit_hash
    return updated


def update_positive_json(
    path: Path,
    coverage_rows: list[dict[str, str]],
) -> None:
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    rows_by_record: dict[int, list[dict[str, str]]] = {}
    for row in coverage_rows:
        rows_by_record.setdefault(int(row["record_index"]), []).append(row)

    updated_records = []
    for record_index, record in enumerate(records, 1):
        source_file = source_file_from_record(record)
        rows = rows_by_record.get(record_index, [])
        project = repository_name(source_file)
        paths = [source_file]
        for row in rows:
            paths.append(row.get("source_file", ""))
            paths.extend(
                test_file
                for test_file in row.get("test_files", "").split(";")
                if test_file
            )
        updated_records.append(
            add_json_commit_hash(record, latest_commit(project, paths))
        )

    path.write_text(
        json.dumps(updated_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_negative_json(path: Path) -> None:
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    updated_records = []
    for record in records:
        source_file = source_file_from_record(record)
        project = repository_name(source_file)
        updated_records.append(
            add_json_commit_hash(
                record,
                latest_commit(project, [source_file]),
            )
        )
    path.write_text(
        json.dumps(updated_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    coverage_by_json = {}
    for coverage_path in sorted(POSITIVE_ROOT.glob("*-test/*_coverage.csv")):
        rows = update_coverage_csv(coverage_path)
        json_path = coverage_path.with_name(
            coverage_path.name.replace("_coverage.csv", ".json")
        )
        coverage_by_json[json_path] = rows

    merged_path = POSITIVE_ROOT / "merged_coverage_all.csv"
    if merged_path.exists():
        update_coverage_csv(merged_path)

    for json_path, rows in coverage_by_json.items():
        update_positive_json(json_path, rows)

    for negative_path in sorted((DATASET_ROOT / "negative").glob("*.json")):
        update_negative_json(negative_path)


if __name__ == "__main__":
    main()
