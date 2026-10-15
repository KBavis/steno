"""Identify (docs/concepts/identity.md §4): the same deployable keeps its Steno ID across
renames, moves, removals, and restores; nothing is merged by guesswork. In memory: each run is
`identify` on what structure found in one repository."""

from steno.identity.identify import Deployable, MemoryRegistry, identify
from steno.identity.names import Name

REPO_A, REPO_B = 1, 2


def _d(path: str, *names: tuple[str, str]) -> Deployable:
    """A deployable at `path`, with its path name and any (name, slot) the build states."""
    folder = path.rsplit("/", 1)[-1] if path != "." else "repo"
    out = [Name(f"repo-{folder}", "path", "CODE")]
    out += [Name(n, slot, "CODE") for n, slot in names]
    return Deployable(path, out)


def _run(reg: MemoryRegistry, repo: int, *deployables: Deployable, job: int = 1):
    ids, report = identify(reg, repo, deployables, job)
    return {p: (i.steno_id, i.canonical) for p, i in ids.items()}, report


def test_same_deployable_keeps_its_id():
    reg = MemoryRegistry()
    first, report = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")))
    assert report.new == ["org-svc-users"]
    again, report = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")), job=2)
    assert again == first and not report.as_dict()


def test_the_name_ladder_picks_the_canonical_name():
    reg = MemoryRegistry()
    ids, _ = _run(
        reg,
        REPO_A,
        _d("users", ("users-service", "runtime"), ("org-svc-users", "build_artifact")),
    )
    # The build artifact name outranks the runtime self-name and the folder
    assert ids["users"][1] == "org-svc-users"
    ids, _ = _run(reg, REPO_A, _d("billing"))
    assert ids["billing"][1] == "repo-billing"  # only the folder: provisional


def test_renamed_in_place_keeps_its_id_and_the_old_name_is_an_alias():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("users-svc", "build_artifact")))
    after, report = _run(reg, REPO_A, _d("users", ("org-svc-identity", "build_artifact")), job=2)
    assert after["users"][0] == before["users"][0]
    assert report.renamed == [{"from": "users-svc", "to": "org-svc-identity"}]
    names = {(n.name, past) for n, past in reg.names[after["users"][0]]}
    assert ("users-svc", True) in names


def test_moved_within_the_repository_keeps_its_id():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")))
    after, report = _run(
        reg, REPO_A, _d("services/users", ("org-svc-users", "build_artifact")), job=2
    )
    assert after["services/users"][0] == before["users"][0]
    assert report.moved and not report.removed and not report.new


def test_moved_to_another_repository_after_the_old_one_removed_it():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")))
    _, report = _run(reg, REPO_A, job=2)  # the old repository no longer builds it
    assert report.removed == ["org-svc-users"]
    after, report = _run(reg, REPO_B, _d(".", ("org-svc-users", "build_artifact")), job=3)
    assert after["."][0] == before["users"][0]
    assert report.restored == ["org-svc-users"] and report.moved


def test_two_repositories_claiming_one_name_is_a_conflict_not_a_merge():
    reg = MemoryRegistry()
    a, _ = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")))
    b, report = _run(reg, REPO_B, _d(".", ("org-svc-users", "build_artifact")), job=2)
    assert b["."][0] != a["users"][0]
    assert report.conflicts == [{"name": "org-svc-users", "with": a["users"][0]}]
    assert reg.records[b["."][0]].conflict_with == a["users"][0]


def test_renamed_and_moved_at_once_is_new_with_a_hint():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("users-svc", "build_artifact")))
    after, report = _run(reg, REPO_A, _d("identity", ("org-svc-identity", "build_artifact")), job=2)
    assert after["identity"][0] != before["users"][0]
    assert report.possible_renames == [{"removed": "users-svc", "added": "org-svc-identity"}]


def test_a_declared_rename_ahead_of_time_keeps_the_id():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("users-svc", "build_artifact")))
    link = {"id": 1, "from_steno_id": before["users"][0], "pending_name": "org-svc-identity"}
    reg.links.append(link)
    after, report = _run(reg, REPO_A, _d("identity", ("org-svc-identity", "build_artifact")), job=2)
    assert after["identity"][0] == before["users"][0]
    assert not report.new and not report.removed


def test_split_keeps_the_id_with_the_part_that_keeps_the_location():
    reg = MemoryRegistry()
    before, _ = _run(reg, REPO_A, _d("users", ("org-svc-users", "build_artifact")))
    after, report = _run(
        reg,
        REPO_A,
        _d("users", ("org-svc-users", "build_artifact")),
        _d("profiles", ("org-svc-profiles", "build_artifact")),
        job=2,
    )
    assert after["users"][0] == before["users"][0]
    assert report.new == ["org-svc-profiles"]


def test_a_platform_deploy_name_linked_exactly_becomes_canonical():
    reg = MemoryRegistry()
    # The XLD repository names the deployable `ledger-svc-prod` and its artifact `ledger-service`
    xld = {"witness": "acme-xld", "file": "deploy/ledger.yaml", "line": 3}
    reg.claims += [
        ("ledger-svc-prod", Name("ledger-svc-prod", "deploy", "PLATFORM", xld)),
        ("ledger-svc-prod", Name("ledger-service", "build_artifact", "PLATFORM", xld)),
    ]
    ids, _ = _run(reg, REPO_A, _d("ledger", ("ledger-service", "build_artifact")))
    steno_id, canonical = ids["ledger"]
    assert canonical == "ledger-svc-prod"  # deploy name outranks the build artifact name
    names = {n.name for n, _ in reg.names[steno_id]}
    assert {"ledger-service", "ledger-svc-prod", "repo-ledger"} <= names


def test_names_that_dont_match_exactly_are_never_linked():
    reg = MemoryRegistry()
    reg.claims.append(("ledger-svc-prod", Name("ledger-svc-prod", "deploy", "PLATFORM")))
    ids, _ = _run(reg, REPO_A, _d("ledger", ("ledger-service", "build_artifact")))
    assert ids["ledger"][1] == "ledger-service"
