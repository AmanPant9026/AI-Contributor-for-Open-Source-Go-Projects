"""Unit tests for eval/metrics.py -- pure scoring, no sandbox required."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import metrics  # noqa: E402

GOLD = (
    "diff --git a/baked_in.go b/baked_in.go\n"
    "--- a/baked_in.go\n+++ b/baked_in.go\n"
    "@@ -1,1 +1,2 @@\n"
    " func x() {\n+\tpostcodeRegexInit.Do(initPostcodes)\n"
)
MULTI = (
    "diff --git a/baked_in.go b/baked_in.go\n--- a/baked_in.go\n+++ b/baked_in.go\n"
    "@@ -1 +1 @@\n-a\n+b\n"
    "diff --git a/regexes.go b/regexes.go\n--- a/regexes.go\n+++ b/regexes.go\n"
    "@@ -1 +1 @@\n-c\n+d\n"
)


def test_files_in_diff_single():
    assert metrics.files_in_diff(GOLD) == {"baked_in.go"}


def test_files_in_diff_multi():
    assert metrics.files_in_diff(MULTI) == {"baked_in.go", "regexes.go"}


def test_files_in_diff_empty():
    assert metrics.files_in_diff("") == set()


def test_localization_perfect():
    r, p = metrics.localization({"baked_in.go"}, {"baked_in.go"})
    assert r == 1.0 and p == 1.0


def test_localization_empty_candidate():
    # empty patch: nothing wrong touched (precision 1.0) but nothing found (recall 0)
    r, p = metrics.localization(set(), {"baked_in.go"})
    assert r == 0.0 and p == 1.0


def test_localization_extra_file_lowers_precision():
    r, p = metrics.localization({"baked_in.go", "unrelated.go"}, {"baked_in.go"})
    assert r == 1.0 and p == 0.5


def test_diff_similarity_identical_high():
    assert metrics.diff_similarity(GOLD, GOLD) == 1.0


def test_diff_similarity_unrelated_low():
    assert metrics.diff_similarity("+totally different line", GOLD) < 0.5


def _score(resolved, status, recall=1.0, precision=1.0, build=True, vet=True, fmt=True):
    return metrics.InstanceScore(
        instance_id="i", candidate="c", status=status, resolved=resolved,
        ftp_passed=resolved, ptp_passed=True, recall=recall, precision=precision,
        build_ok=build, vet_ok=vet, fmt_ok=fmt, diff_similarity=1.0)


def test_resolution_rate():
    scores = [_score(True, metrics.RESOLVED), _score(True, metrics.RESOLVED),
              _score(False, metrics.UNRESOLVED)]
    assert metrics.resolution_rate(scores) == round(2 / 3, 3)
    assert metrics.resolution_rate([]) == 0.0


def test_status_counts():
    scores = [_score(True, metrics.RESOLVED),
              _score(False, metrics.NOOP, recall=None, precision=None,
                     build=None, vet=None, fmt=None),
              _score(False, metrics.APPLY_FAILED, build=None, vet=None, fmt=None)]
    c = metrics.status_counts(scores)
    assert c[metrics.RESOLVED] == 1 and c[metrics.NOOP] == 1 and c[metrics.APPLY_FAILED] == 1


def test_table_renders_na_for_none():
    # a no-op row should print n/a for gates and localization, not ok/0.00
    row = _score(False, metrics.NOOP, recall=None, precision=None,
                 build=None, vet=None, fmt=None)
    table = metrics.format_table([row])
    assert "n/a" in table
    assert "noop" in table
