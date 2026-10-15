"""The claim store (D109; docs/how-it-works/ingestion/claims-and-reconcile.md §5).

Each run replaces its repository's claims: an unchanged claim keeps its row (its line is
updated if it moved), a new one is added, and one no longer found gets `removed_job`, kept for
history and never compared. Re-ingesting the same commit changes no rows. The subjects of
what was added or removed are what reconcile recomputes.

Only other witnesses' claims are stored (PLATFORM, DECLARED). CODE facts stay in the graph,
and CODE names go to the identity registry through identify.
"""

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from steno.claims.types import CLAIM_TYPES, subjects
from steno.db.models import Claim
from steno.extraction.facts import ClaimFact

ADMIN = "admin"


@dataclass
class Stored:
    added: int = 0
    removed: int = 0
    kept: int = 0
    # Subjects of claims added or removed: what reconcile recomputes
    touched: set[str] = field(default_factory=set)


def claim_key(claim_type: str, fields: dict[str, object], file: str | None) -> str:
    """What makes it the same claim next run: its type, fields, and file, not its line."""
    text = json.dumps([claim_type, fields, file], sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:40]


def store_claims(
    session: Session,
    repository_id: int,
    job_id: int,
    commit: str | None,
    claims: list[ClaimFact],
) -> Stored:
    out = Stored()
    current = {
        (c.witness, c.key): c
        for c in session.scalars(
            select(Claim).where(Claim.repository_id == repository_id, Claim.witness != ADMIN)
        )
    }
    seen: set[tuple[str, str]] = set()
    for c in claims:
        if c.witness_type == "CODE":
            continue
        key = claim_key(c.claim_type, c.fields, c.origin.file)
        if (c.witness, key) in seen:
            continue  # the same statement twice in one file
        seen.add((c.witness, key))
        row = current.get((c.witness, key))
        if row is None:
            row = Claim(
                claim_type=c.claim_type,
                facet=CLAIM_TYPES[c.claim_type].facet,
                fields=c.fields,
                subjects=subjects(c.claim_type, c.fields),
                witness_type=c.witness_type,
                witness=c.witness,
                repository_id=repository_id,
                file=c.origin.file,
                key=key,
                first_seen_job=job_id,
            )
            session.add(row)
            current[(c.witness, key)] = row
            out.added += 1
            out.touched.update(row.subjects)
        elif row.removed_job is not None:  # gone, and back
            row.removed_job = None
            out.added += 1
            out.touched.update(row.subjects)
        else:
            out.kept += 1
        row.line = c.origin.line or None
        row.commit = commit
        row.generated = c.generated
        row.last_seen_job = job_id
    for (witness, key), row in current.items():
        if (witness, key) not in seen and row.removed_job is None:
            row.removed_job = job_id
            out.removed += 1
            out.touched.update(row.subjects)
    session.flush()
    return out


def active(session: Session, subject_keys: set[str]) -> list[Claim]:
    """Every claim in force about any of these subjects."""
    if not subject_keys:
        return []
    return list(
        session.scalars(
            select(Claim).where(
                Claim.removed_job.is_(None), Claim.subjects.overlap(sorted(subject_keys))
            )
        )
    )
