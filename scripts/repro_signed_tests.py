#!/usr/bin/env python3
"""repro_signed_tests.py

Reproduce every row in ``dataset/merged_coverage_all.csv`` with the
MuseumTicket bundleName + signingConfigs pre-applied, then compute
method-range coverage and emit a CSV in the **canonical 27-column schema**
shared by ``merged_coverage_all.csv`` and the
``arkts-coverage-csv-export`` skill.

Per row:
  1. Apply value rewrites that previous full runs proved necessary
     (``ohosTest`` → ``test``, ``module=editor`` → ``module=photos_editor``,
     trim ``A+B`` test_suite to first token).
  2. Resolve project root (closest ancestor of ``source_file`` containing
     both ``AppScope/app.json5`` and ``build-profile.json5``).
  3. Idempotently patch ``bundleName`` and ``signingConfigs`` in that
     project (regex handles both quoted and JSON5 unquoted keys; original
     files backed up under ``/tmp/repro_signed_tests_backups/``).
  4. Normalize the test command (strip env-var prefix + absolute hvigor
     path) and ensure ``-p scope=<suite>`` so we never run the whole module.
  5. Execute ``hvigorw <args>`` from the project root with DevEco Studio's
     SDK / Node / hvigor on ``PATH``.
  6. Locate ``coverageReport.json`` + ``test_result.txt`` in the module's
     ``.test`` directory and compute line/branch/function coverage limited
     to ``[range_start, range_end]``.
  7. Append a row in the 27-column schema (+5 diagnostic columns for
     reproducibility) to ``dataset/repro_results.csv``.

Flags: see ``--help``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import arkts_coverage  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ARK_ROOT   = Path('/Users/jiaomingyang/project/arkts')
REPOS_DIR  = ARK_ROOT / 'repos'
INPUT_CSV  = ARK_ROOT / 'dataset' / 'merged_coverage_all.csv'
OUTPUT_CSV = ARK_ROOT / 'dataset' / 'repro_results.csv'
LOG_DIR    = ARK_ROOT / 'dataset' / 'repro_logs'
BACKUP_DIR = Path('/tmp/repro_signed_tests_backups')

DEVECO_APP = Path('/Applications/DevEco-Studio.app')
DEVECO_SDK = DEVECO_APP / 'Contents' / 'sdk'
NODE_HOME  = DEVECO_APP / 'Contents' / 'tools' / 'node'
HVIGOR_BIN = DEVECO_APP / 'Contents' / 'tools' / 'hvigor' / 'bin'

REPO_URL_TPL = 'https://github.com/buaa-icf/{name}.git'

# ---------------------------------------------------------------------------
# MuseumTicket signing material (atomicService debug profile)
# ---------------------------------------------------------------------------
BUNDLE_NAME = 'com.atomicservice.6917603885746666480'

SIGNING_BLOCK = '''[
      {
        "name": "default",
        "type": "HarmonyOS",
        "material": {
          "certpath": "/Users/jiaomingyang/.ohos/config/default_MuseumTicket_ri6fuuzPUdG2fHJqcIl0Khmg5bb3PA6BfCMvYYOYs7o=.cer",
          "keyAlias": "debugKey",
          "keyPassword": "00000019BE11CFF7C692499FB4BE58DEA4C2618F60100B3FEDC342BFAE4F7D3FBBE966E2FDC07BD75B",
          "profile": "/Users/jiaomingyang/.ohos/config/default_MuseumTicket_ri6fuuzPUdG2fHJqcIl0Khmg5bb3PA6BfCMvYYOYs7o=.p7b",
          "signAlg": "SHA256withECDSA",
          "storeFile": "/Users/jiaomingyang/.ohos/config/default_MuseumTicket_ri6fuuzPUdG2fHJqcIl0Khmg5bb3PA6BfCMvYYOYs7o=.p12",
          "storePassword": "00000019796E171871CA8D9AAD79830DA2C295A64DF9750C37213D08ACAA13E1AE108A340A2FBA82C4"
        }
      }
    ]'''

HVIGORW_TOKEN = re.compile(r'(?:^|[\s/])hvigorw(?:\.js)?(?=\s|$)')

# ---------------------------------------------------------------------------
# Canonical 27 columns from arkts-coverage-csv-export
# ---------------------------------------------------------------------------
CANONICAL_COLS = [
    'record_index', 'message_index', 'fragment_role', 'rule', 'source_file',
    'range_start', 'range_end', 'has_test_case', 'test_files', 'test_kind',
    'test_suite', 'test_command', 'test_run_status', 'test_result_file',
    'coverage_report', 'coverage_report_current', 'line_total_in_range',
    'line_covered_in_range', 'line_coverage_pct', 'branch_side_total_in_range',
    'branch_side_covered_in_range', 'branch_coverage_pct',
    'function_total_overlapping_range', 'function_covered_overlapping_range',
    'function_coverage_pct', 'overlapping_coverage_functions', 'note',
]

# Diagnostic suffix columns we keep for reproducibility / debugging
DIAG_COLS = [
    'project_root', 'patch_status', 'test_command_final',
    'exit_code', 'duration_sec', 'log_path', 'rewrite_applied',
]

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def make_env() -> dict:
    env = os.environ.copy()
    env['DEVECO_SDK_HOME'] = str(DEVECO_SDK)
    env['NODE_HOME'] = str(NODE_HOME)
    env['PATH'] = f'{NODE_HOME}/bin:{HVIGOR_BIN}:' + env.get('PATH', '')
    return env

# ---------------------------------------------------------------------------
# Repo cloning
# ---------------------------------------------------------------------------

def unique_top_repos(csv_path: Path) -> list[str]:
    seen: set[str] = set()
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            top = row['source_file'].split('/', 1)[0]
            if top:
                seen.add(top)
    return sorted(seen)


def clone_missing_repos(repos: list[str]) -> None:
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    for name in repos:
        dst = REPOS_DIR / name
        if dst.exists():
            continue
        url = REPO_URL_TPL.format(name=name)
        print(f'[clone] {url}')
        rc = subprocess.run(['git', 'clone', '--depth', '1', url, str(dst)]).returncode
        if rc != 0:
            print(f'[clone] WARN: failed for {name} (rc={rc})')

# ---------------------------------------------------------------------------
# Project-root lookup + signing patch
# ---------------------------------------------------------------------------

def find_project_root(source_file_rel: str) -> Path | None:
    cur = (REPOS_DIR / source_file_rel).parent
    repos_resolved = REPOS_DIR.resolve()
    while True:
        if (cur / 'AppScope' / 'app.json5').exists() and (cur / 'build-profile.json5').exists():
            return cur
        if cur.resolve() == repos_resolved or cur.parent == cur:
            return None
        cur = cur.parent


_PATCHED: set[Path] = set()


def _find_matching_bracket(text: str, open_pos: int) -> int:
    assert text[open_pos] == '['
    depth = 0
    i = open_pos
    while i < len(text):
        ch = text[i]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def patch_project(project: Path) -> str:
    if project in _PATCHED:
        return 'cached'

    app_json = project / 'AppScope' / 'app.json5'
    bp_json  = project / 'build-profile.json5'

    digest = hashlib.sha1(str(project).encode()).hexdigest()[:12]
    bk = BACKUP_DIR / digest
    bk.mkdir(parents=True, exist_ok=True)
    if not (bk / 'app.json5').exists():
        shutil.copy2(app_json, bk / 'app.json5')
        shutil.copy2(bp_json,  bk / 'build-profile.json5')
        (bk / 'project_path.txt').write_text(str(project))

    changed = []

    # bundleName (JSON5: key may be unquoted, value may use single quotes)
    app_text = app_json.read_text()
    new_app  = re.sub(
        r'(["\']?)bundleName\1\s*:\s*["\'][^"\']*["\']',
        f'"bundleName": "{BUNDLE_NAME}"',
        app_text,
        count=1,
    )
    if new_app != app_text:
        app_json.write_text(new_app)
        changed.append('bundleName')

    # signingConfigs (JSON5: key may be unquoted)
    bp_text = bp_json.read_text()
    m = re.search(r'(["\']?)signingConfigs\1\s*:\s*\[', bp_text)
    if m:
        end = _find_matching_bracket(bp_text, m.end() - 1)
        if end > 0:
            new_bp = bp_text[:m.start()] + f'"signingConfigs": {SIGNING_BLOCK}' + bp_text[end:]
            if new_bp != bp_text:
                bp_json.write_text(new_bp)
                bp_text = new_bp
                changed.append('signingConfigs')

    # products[].signingConfig — some projects (e.g. ohos_rive) declare a
    # signingConfigs block but forget to reference it from the product, so
    # hvigor silently skips signing and onDeviceTest fails to find the
    # signed HAP. Inject the reference where missing.
    pm = re.search(r'(["\']?)products\1\s*:\s*\[', bp_text)
    if pm:
        pend = _find_matching_bracket(bp_text, pm.end() - 1)
        if pend > 0:
            products_block = bp_text[pm.end():pend - 1]
            # Each entry looks like `{ "name": "default", ... }`. We
            # inject `"signingConfig": "default",` right after the `name:`
            # line when no signingConfig is present in that entry. Match
            # each `{ ... }` entry at top level via manual brace balance
            # because products entries can contain nested arrays/objects.
            new_block_parts = []
            i = 0
            while i < len(products_block):
                ch = products_block[i]
                if ch != '{':
                    new_block_parts.append(ch)
                    i += 1
                    continue
                # Find matching '}' for this top-level entry
                depth = 0
                j = i
                while j < len(products_block):
                    if products_block[j] == '{':
                        depth += 1
                    elif products_block[j] == '}':
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
                entry = products_block[i:j]
                if re.search(r'(["\']?)signingConfig\1\s*:', entry) is None:
                    entry = re.sub(
                        r'((["\']?)name\2\s*:\s*["\'][^"\']+["\']\s*,)',
                        r'\1\n        "signingConfig": "default",',
                        entry,
                        count=1,
                    )
                    changed.append('product.signingConfig')
                new_block_parts.append(entry)
                i = j
            new_products = ''.join(new_block_parts)
            if new_products != products_block:
                bp_text = bp_text[:pm.end()] + new_products + bp_text[pend - 1:]
                bp_json.write_text(bp_text)

    _PATCHED.add(project)
    return ','.join(changed) if changed else 'noop'


def restore_all() -> None:
    if not BACKUP_DIR.exists():
        print('no backups to restore')
        return
    for d in BACKUP_DIR.iterdir():
        if not d.is_dir():
            continue
        path_file = d / 'project_path.txt'
        if not path_file.exists():
            continue
        project = Path(path_file.read_text().strip())
        for fname in ('app.json5', 'build-profile.json5'):
            src = d / fname
            dst = project / ('AppScope/app.json5' if fname == 'app.json5' else fname)
            if src.exists() and dst.exists():
                shutil.copy2(src, dst)
                print(f'restored {dst}')

# ---------------------------------------------------------------------------
# Value rewrites for known-bad rows in merged_coverage_all.csv
# ---------------------------------------------------------------------------

LEGACY_OHOSTEST_PROJECTS = (
    'BooksAndReferenceTemplate/BookRead',
    'BusinessTemplate/OfficeAttendance',
    'ToolsTemplate/SmartHome',
    'openharmony_tpc_samples/ohos_rive',
    'BusinessTemplate/EnterpriseRecruitment',
)


def rewrite_row(project_root: Path | None, cmd: str, test_suite: str) -> tuple[str, str, str]:
    """Return (new_cmd, new_test_suite, rules_applied). Empty rules_applied
    means no rewrite was needed for this row."""
    rules: list[str] = []
    new_cmd = cmd
    new_suite = test_suite

    proj_str = str(project_root) if project_root else ''

    # 1) Legacy task name `ohosTest` is unregistered in some projects.
    #    The modern hvigor task that runs on-device/emulator tests AND
    #    produces ohosTest-style coverage artifacts is `onDeviceTest`.
    #    (Using `test` instead would silently run zero suites — those live
    #    in `src/ohosTest`, not `src/test`.)
    if any(p in proj_str for p in LEGACY_OHOSTEST_PROJECTS):
        if ' ohosTest ' in (' ' + new_cmd + ' '):
            new_cmd = (' ' + new_cmd + ' ').replace(' ohosTest ', ' onDeviceTest ').strip()
            rules.append('ohosTest->onDeviceTest')

    # 2) applications_photos: the directory is feature/editor but the module
    #    declared in build-profile.json5 is photos_editor.
    if 'applications_photos' in proj_str and '-p module=editor ' in (new_cmd + ' '):
        new_cmd = new_cmd.replace('-p module=editor ', '-p module=photos_editor ')
        if new_cmd.endswith('-p module=editor'):
            new_cmd = new_cmd[:-len('-p module=editor')] + '-p module=photos_editor'
        rules.append('editor->photos_editor')

    # 3) Suite values like `A+B` aren't parsed by hvigor. Treat `+` as a
    #    delimiter and run both suites (hvigor accepts comma-separated).
    if '+' in new_suite and ';' not in new_suite and ',' not in new_suite:
        new_suite = new_suite.replace('+', ',')
        rules.append('plus->comma')

    return new_cmd, new_suite, ','.join(rules)

# ---------------------------------------------------------------------------
# Command normalization (post-rewrite)
# ---------------------------------------------------------------------------

SCOPE_RE = re.compile(r'(?:^|\s)(?:-p\s+scope=|--scope[=\s]+)([^\s]+)')


def normalize_command(raw_cmd: str, test_suite: str) -> tuple[list[str], str, str]:
    """Strip env-var prefix and absolute hvigor path; ensure scope is set."""
    cmd = raw_cmd.replace('\n', ' ').strip().strip('"').strip()
    m = HVIGORW_TOKEN.search(cmd)
    if not m:
        return [], '', cmd
    tail = cmd[m.end():].strip()

    try:
        args = shlex.split(tail)
    except ValueError:
        args = tail.split()

    scope_match = SCOPE_RE.search(' ' + ' '.join(args))
    if scope_match:
        scope = scope_match.group(1)
    elif test_suite:
        # Suites named `*AllSuites` (e.g. `book_personAllSuites`) are dataset
        # placeholders meaning "run every describe() in the module" — not a
        # real `describe(...)` name. Passing them as -p scope= matches no
        # suites and runs zero tests, defeating coverage. For those rows we
        # intentionally leave scope unset so the whole module's suites run,
        # matching the original dataset behavior.
        first = test_suite.split(';')[0].strip()
        if first and first.endswith('AllSuites'):
            scope = ''  # report what we used; do not pass it to hvigor
        elif first:
            scope = first
            args += ['-p', f'scope={scope}']
        else:
            scope = ''
    else:
        scope = ''

    return args, scope, 'hvigorw ' + ' '.join(shlex.quote(a) for a in args)

# ---------------------------------------------------------------------------
# Coverage-artifact lookup
# ---------------------------------------------------------------------------

MODULE_ARG_RE = re.compile(r'(?:^|\s)-p\s+module=([^\s@]+)')


def extract_module(args: list[str]) -> str:
    """Find the module name from `-p module=X` or `-p module=X@target`."""
    joined = ' '.join(shlex.quote(a) for a in args)
    m = MODULE_ARG_RE.search(' ' + joined)
    return m.group(1) if m else ''


_NAME_PATH_RE = re.compile(
    r'(["\']?)name\1\s*:\s*["\']([^"\']+)["\']\s*,\s*'
    r'(["\']?)srcPath\3\s*:\s*["\']([^"\']+)["\']'
)


def module_srcpath(project: Path, module_name: str) -> Path | None:
    """Resolve a module name to its srcPath. Doesn't rely on a real JSON5
    parser; just scans the modules-array section for `name: X, srcPath: Y`
    pairs (the canonical hvigor layout). Robust to nested ``targets`` blocks
    because we never try to balance braces — we just match the two adjacent
    keys."""
    bp = (project / 'build-profile.json5').read_text()
    modules_m = re.search(r'(["\']?)modules\1\s*:\s*\[', bp)
    if not modules_m:
        return None
    end = _find_matching_bracket(bp, modules_m.end() - 1)
    if end < 0:
        return None
    block = bp[modules_m.end():end - 1]
    for m in _NAME_PATH_RE.finditer(block):
        if m.group(2) == module_name:
            return (project / m.group(4)).resolve()
    return None


def detect_task(args: list[str]) -> str:
    """Return the task name (test / ohosTest / onDeviceTest / assembleHap)."""
    for a in args:
        if a in ('test', 'ohosTest', 'onDeviceTest', 'assembleHap', 'assembleHapTest'):
            return a
    return ''


def coverage_artifacts(module_dir: Path, task: str) -> tuple[Path, Path]:
    """Return (test_result_path, coverage_report_path) for the given task."""
    # Tests dropped into .test/default/* — instrument tests under ohosTest/.
    intermediate = 'ohosTest' if task in ('ohosTest', 'onDeviceTest') else 'test'
    test_result = module_dir / '.test/default/intermediates' / intermediate / 'coverage_data' / 'test_result.txt'
    report      = module_dir / '.test/default/outputs' / intermediate / 'reports' / 'coverageReport.json'
    return test_result, report

# ---------------------------------------------------------------------------
# Per-row execution
# ---------------------------------------------------------------------------

def empty_diag(row: dict) -> dict:
    """Start with the source row's canonical columns, but blank the fields
    that record run-specific outcomes — they get repopulated by this run.
    Stale notes/coverage from prior CSV runs shouldn't bleed through."""
    blanked = {
        'note', 'test_result_file', 'coverage_report', 'coverage_report_current',
        'line_total_in_range', 'line_covered_in_range', 'line_coverage_pct',
        'branch_side_total_in_range', 'branch_side_covered_in_range', 'branch_coverage_pct',
        'function_total_overlapping_range', 'function_covered_overlapping_range',
        'function_coverage_pct', 'overlapping_coverage_functions', 'test_run_status',
    }
    return {
        **{c: ('' if c in blanked else row.get(c, '')) for c in CANONICAL_COLS},
        'project_root': '', 'patch_status': '', 'test_command_final': '',
        'exit_code': '', 'duration_sec': '', 'log_path': '',
        'rewrite_applied': '',
    }


# Run-internal cache keyed by (project_root, module, task). Populated only
# after a successful hvigor run that produced a coverage report; lets later
# duplicate rows (e.g. code-clone original/similar both running the same
# *AllSuites task) skip a redundant — and on-device, flaky — re-run.
# Process-local by design, so cross-invocation --resume semantics are unchanged.
_RUN_CACHE: dict[tuple, dict] = {}


def _cache_fresh(cached: dict, task: str) -> bool:
    """True when the cached coverage report still exists and its mtime matches
    the value recorded when we generated it — i.e. it is the artifact this run
    produced and nothing has clobbered it since."""
    _, report = coverage_artifacts(cached['module_dir'], task)
    return report.exists() and report.stat().st_mtime == cached['report_mtime']


def _compute_coverage(base: dict, src: str, row: dict, module_dir: Path, task: str) -> None:
    """Populate the coverage columns on ``base`` from the module's coverage
    artifacts. Shared by the fresh-run path and the run-cache reuse path."""
    test_result, report = coverage_artifacts(module_dir, task)
    base['test_result_file']        = str(test_result) if test_result.exists() else ''
    base['coverage_report']         = str(report) if report.exists() else ''
    base['coverage_report_current'] = arkts_coverage.coverage_report_current(str(report)) if report.exists() else 'no'

    if not report.exists():
        return
    try:
        rep = arkts_coverage.load_report(str(report))
    except (OSError, json.JSONDecodeError) as e:
        base['note'] = (base.get('note', '') + f' | coverage_load_error: {e}').strip(' |')
        return

    # source_file in CSV is relative to repos/; resolve to abs
    source_abs = str((REPOS_DIR / src).resolve())
    try:
        rstart = int(row['range_start']); rend = int(row['range_end'])
    except (ValueError, KeyError):
        rstart = rend = 0
    cov = arkts_coverage.compute(rep, source_abs, rstart, rend) if rstart and rend else None
    if cov is None:
        base['note'] = (base.get('note', '') + ' | source_file not in coverage report').strip(' |')
        return

    base['line_total_in_range']               = str(cov.line_total)
    base['line_covered_in_range']             = str(cov.line_covered)
    base['line_coverage_pct']                 = cov.line_pct
    base['branch_side_total_in_range']        = str(cov.branch_total)
    base['branch_side_covered_in_range']      = str(cov.branch_covered)
    base['branch_coverage_pct']               = cov.branch_pct
    base['function_total_overlapping_range']  = str(cov.function_total)
    base['function_covered_overlapping_range']= str(cov.function_covered)
    base['function_coverage_pct']             = cov.function_pct
    base['overlapping_coverage_functions']    = cov.overlapping_functions
    extra_note = arkts_coverage.parse_pass_summary(str(test_result))
    note_parts = [n for n in (extra_note, cov.note) if n]
    base['note'] = '; '.join(note_parts)


def run_one(row: dict, timeout: int) -> dict:
    src = row['source_file']
    proj = find_project_root(src)
    base = empty_diag(row)

    if proj is None:
        base['test_run_status'] = 'error_no_project_root'
        return base
    base['project_root'] = str(proj)

    # Apply known-good rewrites first so the recorded test_command is the
    # one we actually ran.
    # Important: do NOT overwrite base['test_suite'] with the rewritten
    # value — the dedup key in --resume uses test_suite from the source
    # CSV, so the output row must carry the original suite. The rewritten
    # value is only used internally (as the scope argument).
    new_cmd, new_suite, rewrites = rewrite_row(proj, row['test_command'], row['test_suite'])
    base['rewrite_applied'] = rewrites

    base['patch_status'] = patch_project(proj)

    args, scope, normalized = normalize_command(new_cmd, new_suite)
    base['test_command_final'] = normalized

    if not args:
        base['test_run_status'] = 'error_unparseable_command'
        return base
    # Empty scope is intentional for AllSuites-style rows — they need the
    # whole module's suites to run. Only flag missing scope when the input
    # actually lacks a test_suite hint.
    if not scope and not new_suite:
        base['test_run_status'] = 'error_no_scope'
        return base

    # We always record the *rewritten* command in the canonical column so
    # the row reflects what we ran.
    base['test_command'] = new_cmd

    module_name = extract_module(args)
    task = detect_task(args)
    cache_key = (str(proj), module_name, task)

    # Run-internal dedup: when an earlier row in *this* process already ran the
    # exact same module+task+command and produced a coverage report, reuse those
    # artifacts instead of re-running hvigor. Besides saving time, this avoids
    # the flakiness of running the same onDeviceTest suite twice back-to-back
    # (observed: a module's second consecutive on-device run reporting 1/3
    # instead of 3/3). We require the normalized command to match and the cached
    # report to still be the one we generated (mtime unchanged).
    cached = _RUN_CACHE.get(cache_key)
    if module_name and cached and cached['normalized'] == normalized \
            and _cache_fresh(cached, task):
        base['log_path']        = cached['log_path']
        base['exit_code']       = cached['exit_code']
        base['duration_sec']    = '0.0'
        base['test_run_status'] = 'passed'
        _compute_coverage(base, src, row, cached['module_dir'], task)
        base['note'] = ('reused cached run'
                        + (f"; {base['note']}" if base['note'] else ''))
        return base

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_name = (
        f"{row['record_index']}_{row['message_index']}_{row['fragment_role']}_"
        f"{re.sub(r'[^A-Za-z0-9_-]+', '_', scope)[:40]}.log"
    )
    log_path = LOG_DIR / log_name
    base['log_path'] = str(log_path)

    cmd = ['hvigorw', *args]
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(proj), env=make_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
        out = proc.stdout.decode('utf-8', 'replace')
        rc = proc.returncode
        if rc == 0:
            status = 'passed'
        else:
            lower = out.lower()
            if 'compile_failed' in lower or 'arkts compiler error' in lower:
                status = 'failed_compile'
            elif 'profile is invalid' in lower or 'verify profile signature failed' in lower:
                status = 'failed_sign'
            elif ('no device' in lower or 'no connected device' in lower
                  or 'need connect-key' in lower or '00507013' in out):
                status = 'failed_no_device'
            else:
                status = 'failed'
    except subprocess.TimeoutExpired as e:
        out = (e.stdout.decode('utf-8', 'replace') if e.stdout else '') + '\n[TIMEOUT]'
        rc = -1
        status = 'timeout'
    except FileNotFoundError as e:
        out = f'[FileNotFound] {e}'
        rc = -2
        status = 'error_no_hvigorw'
    except Exception as e:  # pylint: disable=broad-except
        out = f'[EXEC ERROR] {e}'
        rc = -3
        status = 'error_exec'

    log_path.write_text(out)
    base['exit_code']    = str(rc)
    base['duration_sec'] = str(round(time.time() - start, 1))
    base['test_run_status'] = status

    # ---- Coverage extraction (best-effort; only meaningful when passed) -
    if status == 'passed' and module_name:
        module_dir = module_srcpath(proj, module_name)
        if module_dir and module_dir.exists():
            _compute_coverage(base, src, row, module_dir, task)
            _, report = coverage_artifacts(module_dir, task)
            if report.exists():
                _RUN_CACHE[cache_key] = {
                    'module_dir':   module_dir,
                    'normalized':   normalized,
                    'log_path':     base['log_path'],
                    'exit_code':    base['exit_code'],
                    'report_mtime': report.stat().st_mtime,
                }

    return base

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

ROW_KEY = ('record_index', 'message_index', 'fragment_role',
           'source_file', 'range_start', 'range_end', 'test_suite')


def _key(row: dict) -> tuple:
    return tuple(row.get(k, '') for k in ROW_KEY)


def load_done(out_path: Path) -> set[tuple]:
    done: set[tuple] = set()
    if out_path.exists():
        with out_path.open() as f:
            for row in csv.DictReader(f):
                done.add(_key(row))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=['local-test', 'instrument-test'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--timeout', type=int, default=1200)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--no-clone', action='store_true')
    ap.add_argument('--input',  default=str(INPUT_CSV))
    ap.add_argument('--output', default=str(OUTPUT_CSV))
    ap.add_argument('--restore', action='store_true',
                    help='Restore backed-up project files and exit')
    args = ap.parse_args()

    if args.restore:
        restore_all()
        return 0

    input_path = Path(args.input)
    if not args.no_clone:
        clone_missing_repos(unique_top_repos(input_path))

    out_path = Path(args.output)
    done = load_done(out_path) if args.resume else set()

    selected: list[dict] = []
    with input_path.open() as f:
        for row in csv.DictReader(f):
            if args.kind and row['test_kind'] != args.kind:
                continue
            # Resume key uses the *original* test_suite from the input.
            if _key(row) in done:
                continue
            selected.append(row)
            if args.limit and len(selected) >= args.limit:
                break

    print(f'[plan] {len(selected)} rows to process '
          f'(kind={args.kind or "any"}, dry_run={args.dry_run})')

    if args.dry_run:
        for i, row in enumerate(selected, 1):
            proj = find_project_root(row['source_file'])
            new_cmd, new_suite, rewrites = rewrite_row(proj, row['test_command'], row['test_suite'])
            _, scope, normalized = normalize_command(new_cmd, new_suite)
            tag = f' [rewrite: {rewrites}]' if rewrites else ''
            print(f'[{i}/{len(selected)}] proj={proj}{tag}')
            print(f'  scope={scope!r} cmd={normalized[:200]}')
        return 0

    fieldnames = CANONICAL_COLS + DIAG_COLS

    new_file = not out_path.exists()
    with out_path.open('a', newline='') as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, extrasaction='ignore')
        if new_file:
            writer.writeheader()
            fout.flush()
        for i, row in enumerate(selected, 1):
            print(f'[{i}/{len(selected)}] {row["source_file"]} :: {row["test_suite"]}')
            res = run_one(row, args.timeout)
            writer.writerow(res)
            fout.flush()
            print(f"    -> {res['test_run_status']} rc={res['exit_code']} "
                  f"patch={res['patch_status']} rewrite={res['rewrite_applied'] or '-'} "
                  f"line%={res.get('line_coverage_pct') or '-'} t={res['duration_sec']}s")

    return 0


if __name__ == '__main__':
    sys.exit(main())
