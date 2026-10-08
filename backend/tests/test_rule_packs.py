"""Every rule pack's test cases (rule-packs/*/*/tests), run as part of `make test`."""

import shutil

import pytest

from steno.config import get_settings
from steno.rule_packs.packs import load_packs
from steno.rule_packs.testing import run_case

PACKS = load_packs(get_settings().rule_packs_dir)
CASES = [
    (pack, rule_dir.name, case_dir)
    for pack in PACKS
    for rule_dir in sorted((pack.path / "tests").glob("*/"))
    for case_dir in sorted(rule_dir.iterdir())
    if (case_dir / "expected.yaml").exists()
]


@pytest.mark.parametrize(
    ("pack", "rule_id", "case_dir"), CASES, ids=[f"{p.name}/{r}/{c.name}" for p, r, c in CASES]
)
def test_case(pack, rule_id, case_dir):
    if pack.language == "java" and not _jvm_helper():
        pytest.skip("Java rules need the JVM helper (make resolver)")
    result = run_case(pack, rule_id, case_dir, PACKS)
    assert result.passed, {
        "missing": result.missing,
        "unexpected": result.unexpected,
        "errors": result.errors,
    }


def _jvm_helper() -> bool:
    return (get_settings().resolver_jvm_dir / "lib").is_dir() and shutil.which("java") is not None
