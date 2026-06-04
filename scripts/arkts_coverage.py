"""arkts_coverage.py — Port of export_coverage_csv.js coverage-math.

Loads a hvigor `coverageReport.json` once, then `compute(...)` returns the
27-column-schema coverage fields for a given (source_file, range_start,
range_end) target.

Only the math is here; the CSV layer is in `repro_signed_tests.py`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CoverageRow:
    line_total: int
    line_covered: int
    line_pct: str
    branch_total: int
    branch_covered: int
    branch_pct: str
    function_total: int
    function_covered: int
    function_pct: str
    overlapping_functions: str
    note: str  # extra hint, e.g. "existing test artifacts do not cover this target range"


def _pct(covered: int, total: int) -> str:
    return 'N/A' if total == 0 else f'{covered * 100 / total:.2f}'


def _norm(p: str) -> str:
    return os.path.normpath(p)


def _find_file(report: dict, source_abs: str) -> dict | None:
    """Look up the file entry in the coverage report.

    Tries (in order):
      1. exact normalized-path match
      2. either-direction endsWith
      3. progressive suffix-segment match — drops leading components until
         either path's tail covers the other. This tolerates dataset bugs
         like the `cases/CommonAppDevelopment/CommonAppDevelopment/...`
         doubled segment in some source_file cells.
    """
    target = _norm(source_abs)
    files = report.get('files', [])
    for f in files:
        if _norm(f.get('path', '')) == target:
            return f
    for f in files:
        cp = _norm(f.get('path', ''))
        if target.endswith(cp) or cp.endswith(target):
            return f

    # Suffix-segment match: pick the entry sharing the longest matching
    # trailing run of path segments with the target (require ≥3 segments
    # so we don't accidentally pair files with the same basename).
    target_segs = target.split(os.sep)
    best: tuple[int, dict | None] = (0, None)
    for f in files:
        cp_segs = _norm(f.get('path', '')).split(os.sep)
        n = 0
        for a, b in zip(reversed(target_segs), reversed(cp_segs)):
            if a == b:
                n += 1
            else:
                break
        if n > best[0]:
            best = (n, f)
    if best[1] is not None and best[0] >= 3:
        return best[1]
    return None


def _regions_intersect(region: dict, start: int, end: int) -> bool:
    rs = region.get('startLoc', {}).get('line')
    re_ = region.get('endLoc', {}).get('line')
    if rs is None or re_ is None:
        return False
    return rs <= end and re_ >= start


def compute(report: dict, source_abs: str, start: int, end: int) -> CoverageRow | None:
    """Return the coverage row for this target, or None if the file is not
    in the coverage report at all."""
    file = _find_file(report, source_abs)
    if file is None:
        return None

    line_counts: list[int] = (file.get('summary', {}).get('lines', {}).get('executedLineCount', []) or [])

    line_total = 0
    line_covered = 0
    for line in range(start, end + 1):
        idx = line - 1
        if idx < 0 or idx >= len(line_counts):
            continue
        count = line_counts[idx]
        if count is None or count < 0:
            continue
        line_total += 1
        if count > 0:
            line_covered += 1

    overlapping: list[dict] = []
    for fn in (file.get('functions') or []):
        regions = fn.get('regions') or []
        if any(_regions_intersect(r, start, end) for r in regions):
            overlapping.append(fn)

    branch_total = 0
    branch_covered = 0
    for fn in overlapping:
        for branch in (fn.get('branches') or []):
            line = (branch.get('startLoc') or {}).get('line')
            if line is None or line < start or line > end:
                continue
            branch_total += 2
            if (branch.get('trueCount') or 0) > 0:
                branch_covered += 1
            if (branch.get('falseCount') or 0) > 0:
                branch_covered += 1

    function_covered = sum(1 for fn in overlapping if (fn.get('count') or 0) > 0)
    names = ';'.join(fn.get('name', '') for fn in overlapping)

    note = ''
    if overlapping and function_covered == 0:
        note = 'existing test artifacts do not cover this target range'

    return CoverageRow(
        line_total=line_total,
        line_covered=line_covered,
        line_pct=_pct(line_covered, line_total),
        branch_total=branch_total,
        branch_covered=branch_covered,
        branch_pct=_pct(branch_covered, branch_total),
        function_total=len(overlapping),
        function_covered=function_covered,
        function_pct=_pct(function_covered, len(overlapping)),
        overlapping_functions=names,
        note=note,
    )


def coverage_report_current(path: str) -> str:
    """Returns the `yes_YYYY-MM-DD_HH:MM:SS` stamp used in the CSV's
    `coverage_report_current` column."""
    if not os.path.exists(path):
        return 'no'
    mtime = datetime.fromtimestamp(os.stat(path).st_mtime)
    return f'yes_{mtime.strftime("%Y-%m-%d_%H:%M:%S")}'


def parse_pass_summary(test_result_path: str) -> str:
    """Returns the `rerun passed: N/M tests` summary string. Empty if the
    test_result.txt is missing or malformed."""
    if not os.path.exists(test_result_path):
        return ''
    try:
        text = open(test_result_path).read()
    except OSError:
        return ''
    import re
    m = re.search(r'Tests run:\s*(\d+),\s*Failure:\s*(\d+),\s*Error:\s*(\d+),\s*Pass:\s*(\d+),\s*Ignore:\s*(\d+)', text)
    if not m:
        return ''
    total, _fail, _err, passed, _ignore = m.groups()
    return f'rerun passed: {passed}/{total} tests'


def load_report(path: str) -> dict[str, Any]:
    with open(path, encoding='utf-8') as f:
        return json.load(f)
