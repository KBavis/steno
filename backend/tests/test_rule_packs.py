"""Every rule pack's test cases (rule-packs/*/*/tests), run as part of `make test`."""

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
    result = run_case(pack, rule_id, case_dir, PACKS)
    assert result.passed, {
        "missing": result.missing,
        "unexpected": result.unexpected,
        "errors": result.errors,
    }
