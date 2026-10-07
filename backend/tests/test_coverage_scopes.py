"""The coverage report's scopes: an organization with nested spaces, many applications, and
one application no space owns."""

from types import SimpleNamespace

from steno.api.routes.coverage import _breadcrumbs, _children, _within

SPACES = {
    1: SimpleNamespace(id=1, name="Payments", parent_id=None),
    2: SimpleNamespace(id=2, name="Payroll", parent_id=1),
    3: SimpleNamespace(id=3, name="Identity", parent_id=None),
}
APPS = {
    "billing": {"space": 1},
    "paychecks": {"space": 2},
    "taxes": {"space": 2},
    "auth": {"space": 3},
    "scratch": {"space": None},
}


def _rows(kind, key, in_scope):
    return {r["label"]: sorted(r["apps"]) for r in _children(kind, key, APPS, SPACES, in_scope)}


def test_a_space_includes_its_sub_spaces():
    assert {a for a in APPS if _within(APPS[a]["space"], 1, SPACES)} == {
        "billing",
        "paychecks",
        "taxes",
    }
    assert {a for a in APPS if _within(APPS[a]["space"], None, SPACES)} == {"scratch"}


def test_the_organization_lists_top_level_spaces_and_unowned_applications():
    assert _rows("org", "", set(APPS)) == {
        "Identity": ["auth"],
        "Payments": ["billing", "paychecks", "taxes"],
        "No space": ["scratch"],
    }


def test_a_space_lists_its_sub_spaces_and_its_own_applications():
    in_payments = {"billing", "paychecks", "taxes"}
    assert _rows("space", "1", in_payments) == {
        "Payroll": ["paychecks", "taxes"],
        "billing": ["billing"],
    }


def test_breadcrumbs_follow_the_space_tree():
    labels = [c["label"] for c in _breadcrumbs("app", "taxes", APPS, SPACES)]
    assert labels == ["Organization", "Payments", "Payroll", "taxes"]
