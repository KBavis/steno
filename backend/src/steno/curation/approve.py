"""Approving or rejecting a proposal (D120, D125, D129).

Approving a rule writes it, with its test cases, into the pack it belongs in, and runs that
pack's tests before anything is kept:

- an existing pack with the suggested name: the rule is added to it;
- otherwise a new pack: a **core** pack under `rule-packs/<ecosystem>/` for a public library,
  an **organization** pack (the first `STENO_RULE_PACK_DIRS` folder, else `rule-packs/org/`)
  for an internal framework (D129).

With `commit`, the files are committed as Steno in the repository that holds the pack (D125).
Approving "ignore" ignores the pattern; approving "vocabulary" sends it to the backlog (the
pattern stays blocked until the vocabulary is built); "enable the pack" is noted for a person
to act on. Rejecting records why, and the pattern is `rejected`.
"""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.coverage.patterns import triage
from steno.db.models import (
    CoveragePattern,
    PatternState,
    ProposalKind,
    ProposalStatus,
    RuleProposal,
)
from steno.rule_packs.packs import load_all_packs, load_pack
from steno.rule_packs.testing import run_pack_tests

STENO_AUTHOR = "Steno <steno@localhost>"


class ApprovalError(ValueError):
    pass


def approve(
    session: Session, proposal: RuleProposal, by: str, commit: bool = False
) -> dict[str, Any]:
    if proposal.status != ProposalStatus.READY:
        raise ApprovalError(f"proposal #{proposal.id} is {proposal.status.value}, not ready")
    out: dict[str, Any] = {}
    if proposal.kind == ProposalKind.RULE:
        out = _write_rule(proposal)
        if commit:
            out["commit"] = _commit(out["pack_dir"], proposal, by)
            proposal.commit_sha = out["commit"]
        state = PatternState.APPROVED
    elif proposal.kind == ProposalKind.IGNORE:
        triage(session, list(proposal.fingerprints), PatternState.IGNORED,
               f"proposal #{proposal.id} approved by {by}: {proposal.summary}")  # fmt: skip
        state = PatternState.IGNORED
    elif proposal.kind == ProposalKind.VOCABULARY:
        state = PatternState.BLOCKED_VOCABULARY  # waits for the vocabulary to be built
    else:  # enable_pack: a person turns the pack on (or widens its versions)
        state = PatternState.APPROVED
    proposal.status = ProposalStatus.APPROVED
    proposal.reviewed_by = by
    proposal.reviewed_at = datetime.now(UTC)
    _set_patterns(session, proposal, state, f"proposal #{proposal.id} approved by {by}")
    return out


def reject(session: Session, proposal: RuleProposal, by: str, why: str) -> None:
    if proposal.status not in (ProposalStatus.READY, ProposalStatus.NEEDS_PERSON):
        raise ApprovalError(f"proposal #{proposal.id} is {proposal.status.value}")
    proposal.status = ProposalStatus.REJECTED
    proposal.reviewed_by = by
    proposal.reviewed_at = datetime.now(UTC)
    proposal.comment = why
    _set_patterns(session, proposal, PatternState.REJECTED, f"rejected by {by}: {why}")


def _set_patterns(session: Session, proposal: RuleProposal, state: PatternState, note: str) -> None:
    for p in session.scalars(
        select(CoveragePattern).where(CoveragePattern.fingerprint.in_(proposal.fingerprints))
    ):
        p.state = state
        p.note = note


# ---------------------------------------------------------------- writing


def pack_meta(proposal: RuleProposal) -> dict[str, Any]:
    """The pack.yaml for a new pack: the drafter's suggestion, completed deterministically
    when it's missing (language from the test files, enabled by imports of the owner)."""
    meta = dict(proposal.pack or {})
    files = [f for case in (proposal.tests or {}).values() for f in (case.get("files") or {})]
    language = meta.get("language") or (
        "python" if files and all(f.endswith(".py") for f in files) else "java"
    )
    owner = (proposal.owner or "").split(".")[0] if language == "python" else proposal.owner
    return {
        "name": meta.get("name") or proposal.target_pack or f"steno-pack-proposal-{proposal.id}",
        "version": str(meta.get("version", "0.1.0")),
        "language": language,
        "source": meta.get("source", "org"),
        "description": meta.get("description") or proposal.summary,
        "enabled_when": meta.get("enabled_when")
        or ({"imports": [owner]} if owner else {"always": True}),
    }


def _write_rule(proposal: RuleProposal) -> dict[str, Any]:
    if not proposal.rule:
        raise ApprovalError(f"proposal #{proposal.id} has no rule")
    rule = yaml.safe_load(proposal.rule)
    existing = {p.name: p for p in load_all_packs()}
    meta = pack_meta(proposal)
    created = meta["name"] not in existing
    pack_dir = existing[meta["name"]].path if not created else _new_pack_dir(meta)
    rule_file = pack_dir / "rules" / f"{rule['id']}.yaml"
    if rule_file.exists():
        raise ApprovalError(f"{rule_file} already exists")
    written: list[Path] = []
    try:
        if created:
            pack_dir.mkdir(parents=True)
            (pack_dir / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
            written.append(pack_dir)
        rule_file.parent.mkdir(parents=True, exist_ok=True)
        rule_file.write_text(proposal.rule.strip() + "\n")
        written.append(rule_file)
        for case, body in (proposal.tests or {}).items():
            case_dir = pack_dir / "tests" / rule["id"] / case
            for rel, text in (body.get("files") or {}).items():
                path = case_dir / "input" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (case_dir / "input").mkdir(parents=True, exist_ok=True)
            (case_dir / "expected.yaml").write_text(
                yaml.safe_dump(body.get("expected") or {}, sort_keys=False)
            )
        written.append(pack_dir / "tests" / rule["id"])
        pack = load_pack(pack_dir)
        results = run_pack_tests(
            pack, [*[p for p in existing.values() if p.name != pack.name], pack]
        )
        failed = [f"{r.rule}/{r.case}" for r in results if not r.passed]
        if failed:
            raise ApprovalError(f"the pack's tests fail with the rule in place: {failed}")
    except Exception:
        for path in reversed(written):  # leave the packs as they were
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(
                missing_ok=True
            )
        raise
    return {"pack": meta["name"], "pack_dir": pack_dir, "created_pack": created,
            "rule_file": rule_file, "cases": len(proposal.tests or {})}  # fmt: skip


def _new_pack_dir(meta: dict[str, Any]) -> Path:
    settings = get_settings()
    if meta.get("source") == "core":
        return settings.rule_packs_dir / meta["language"] / meta["name"]
    root = (
        settings.rule_pack_dirs[0] if settings.rule_pack_dirs else settings.rule_packs_dir / "org"
    )
    return root / meta["name"]


def _commit(pack_dir: Path, proposal: RuleProposal, by: str) -> str:
    """Commit the pack's changes as Steno in the repository that holds it (D125)."""
    top = subprocess.run(
        ["git", "-C", str(pack_dir), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    repo = Path(top.stdout.strip())
    subprocess.run(["git", "-C", str(repo), "add", "--", str(pack_dir)], check=True)
    message = (
        f"rules: {proposal.title} (proposal #{proposal.id})\n\n{proposal.summary}\n\n"
        f"Drafted by Steno's rule curation ({proposal.drafter}), proven by its test cases, "
        f"approved by {by}."
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--author", STENO_AUTHOR,
                    "-m", message, "--", str(pack_dir)], check=True)  # fmt: skip
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()  # fmt: skip
