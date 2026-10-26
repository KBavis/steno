"""Approving or rejecting a proposal (D120, D125, D129, D131).

Approving a rule writes it, with its test cases, into the pack it belongs in, runs that pack's
tests, and publishes it (`publish.py`), all in Steno's own worktree of the destination, never
in a checkout someone is using:

- an existing pack with the suggested name: the rule is added to it;
- otherwise a new pack: a **core** pack under `rule-packs/<ecosystem>/` for a public library,
  an **organization** pack (the first `STENO_RULE_PACK_DIRS` folder, else `rule-packs/org/`)
  for an internal framework (D129).

Publishing collects approved rules on the `steno/rules` branch with one open pull request per
destination, or pushes to its default branch, as the destination is set (D131).
Approving "ignore" ignores the pattern; approving "vocabulary" sends it to the backlog (the
pattern stays blocked until the vocabulary is built); "enable the pack" is noted for a person
to act on. Rejecting records why, and the pattern is `rejected`.
"""

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.config import get_settings
from steno.coverage.patterns import triage
from steno.curation.publish import PublishError, destination, publish, worktree
from steno.db.models import (
    CoveragePattern,
    IgnoredFile,
    PatternState,
    ProposalKind,
    ProposalStatus,
    RuleProposal,
)
from steno.rule_packs.packs import load_all_packs, load_pack
from steno.rule_packs.testing import run_pack_tests


class ApprovalError(ValueError):
    pass


def approve(session: Session, proposal: RuleProposal, by: str) -> dict[str, Any]:
    if proposal.status != ProposalStatus.READY:
        raise ApprovalError(f"proposal #{proposal.id} is {proposal.status.value}, not ready")
    out: dict[str, Any] = {}
    if proposal.kind == ProposalKind.RULE:
        out = _publish_rule(proposal, by)
        proposal.commit_sha = out["commit"]
        proposal.published = out
        state = PatternState.APPROVED
    elif proposal.kind == ProposalKind.IGNORE:
        note = f"proposal #{proposal.id} approved by {by}: {proposal.summary}"
        names = ignore_scope(proposal)
        for fp in proposal.fingerprints:
            for name in names.get(fp, []):
                if (
                    session.scalar(
                        select(IgnoredFile).where(
                            IgnoredFile.fingerprint == fp, IgnoredFile.name == name
                        )
                    )
                    is None
                ):
                    session.add(IgnoredFile(fingerprint=fp, name=name, note=note))
        triage(session, list(proposal.fingerprints), PatternState.IGNORED, note)
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


def ignore_scope(proposal: RuleProposal) -> dict[str, list[str]]:
    """What an "ignore" covers, for unread-files patterns: only the file names the drafter
    examined (its samples), not every file the pattern groups (`*.yml` at a repository's
    root, in every repository). Other patterns are ignored whole."""
    names = sorted({PurePosixPath(s["file"]).name for s in proposal.samples or [] if s.get("file")})
    return {
        fp: names
        for fp in proposal.fingerprints
        if fp.startswith("unread_file:") and not fp.endswith("@*") and names
    }


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
        "witness_type": str(meta.get("witness_type") or "CODE").upper(),
        "enabled_when": meta.get("enabled_when")
        or ({"imports": [owner]} if owner else {"always": True}),
    }


def target(proposal: RuleProposal) -> tuple[Path, str, bool]:
    """Where an approved rule goes: its pack's folder (as it is in the checkout Steno loads
    packs from), the pack's source (core or org), and whether the pack is new."""
    meta = pack_meta(proposal)
    existing = {p.name: p for p in load_all_packs()}
    if meta["name"] in existing:
        pack = existing[meta["name"]]
        return pack.path, pack.source, False
    return _new_pack_dir(meta, (proposal.pack or {}).get("folder")), meta["source"], True


def _publish_rule(proposal: RuleProposal, by: str) -> dict[str, Any]:
    if not proposal.rule:
        raise ApprovalError(f"proposal #{proposal.id} has no rule")
    rule = yaml.safe_load(proposal.rule)
    meta = pack_meta(proposal)
    pack_dir, source, created = target(proposal)
    try:
        dest = destination(source, pack_dir)
        tree = worktree(dest)
    except PublishError as exc:
        raise ApprovalError(f"can't publish: {exc}") from exc
    pack_in_tree = tree / pack_dir.resolve().relative_to(dest.repo.resolve())
    rule_file = pack_in_tree / "rules" / f"{rule['id']}.yaml"
    if rule_file.exists():
        raise ApprovalError(f"a rule {rule['id']} already exists in {meta['name']}")
    try:
        if created:
            pack_in_tree.mkdir(parents=True, exist_ok=True)
            (pack_in_tree / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        rule_file.parent.mkdir(parents=True, exist_ok=True)
        rule_file.write_text(proposal.rule.strip() + "\n")
        for case, body in (proposal.tests or {}).items():
            case_dir = pack_in_tree / "tests" / rule["id"] / case
            for rel, text in (body.get("files") or {}).items():
                path = case_dir / "input" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (case_dir / "input").mkdir(parents=True, exist_ok=True)
            (case_dir / "expected.yaml").write_text(
                yaml.safe_dump(body.get("expected") or {}, sort_keys=False)
            )
        pack = load_pack(pack_in_tree)
        others = [p for p in load_all_packs() if p.name != pack.name]
        failed = [
            f"{r.rule}/{r.case}" for r in run_pack_tests(pack, [*others, pack]) if not r.passed
        ]
        if failed:
            raise ApprovalError(f"the pack's tests fail with the rule in place: {failed}")
        title = f"rules: {rule['id']} in {meta['name']} (proposal #{proposal.id})"
        body = (
            f"{proposal.summary}\n\nDrafted by Steno's rule curation ({proposal.drafter}), "
            f"proven by its {len(proposal.tests or {})} test cases, approved by {by}."
        )
        published = publish(dest, tree, [pack_in_tree], title, body)
    except Exception:
        # Leave Steno's worktree as the remote has it; nothing half-written survives
        subprocess.run(["git", "-C", str(tree), "reset", "--quiet", "--hard"], check=False)
        subprocess.run(["git", "-C", str(tree), "clean", "-fdq"], check=False)
        raise
    return {
        "pack": meta["name"],
        "created_pack": created,
        "rule": rule["id"],
        "destination": dest.name,
        "repository": str(dest.repo),
        "mode": published.mode,
        "branch": published.branch,
        "commit": published.commit,
        "pr_url": published.pr_url,
        "compare_url": published.compare_url,
    }


def _new_pack_dir(meta: dict[str, Any], folder: str | None = None) -> Path:
    """A new core pack goes in the ecosystem folder the drafter chose (an existing one, or a
    new name such as `platform`); without one, its language's folder, or `platform` for a
    pack tied to no language. Org packs go in the organization's pack repository."""
    settings = get_settings()
    if meta.get("source") == "core":
        if not folder or not re.fullmatch(r"[a-z][a-z0-9-]*", folder) or folder in ("org", "core"):
            folder = meta["language"] if meta["language"] != "any" else "platform"
        return settings.rule_packs_dir / folder / meta["name"]
    root = (
        settings.rule_pack_dirs[0] if settings.rule_pack_dirs else settings.rule_packs_dir / "org"
    )
    return root / meta["name"]
