"""Semantic search over flow digests, with a local model: proposes candidates, never decides.

Digests stay on the machine: the model runs here and the vectors live in Neo4j (D23). Optional:
`pip install -e '.[semantic]'` and `STENO_EMBEDDINGS_ENABLED=true`. Without it, or if the model
can't load, routing proposes from keyword search and names, and says so.
"""

import hashlib
import logging
from functools import lru_cache
from typing import Any

from neo4j import Driver

from steno.config import get_settings

log = logging.getLogger(__name__)

FLOW_INDEX = "flow_digest_embedding"
BATCH = 64
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # what BGE models expect


class EmbeddingsUnavailable(RuntimeError):
    """The optional package or the model isn't there."""


@lru_cache(maxsize=1)
def _model() -> Any:
    settings = get_settings()
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise EmbeddingsUnavailable("install it with: pip install -e '.[semantic]'") from exc
    try:
        return TextEmbedding(
            settings.embeddings_model,
            cache_dir=str(settings.embeddings_cache_dir) if settings.embeddings_cache_dir else None,
        )
    except Exception as exc:  # a download or a corrupt file: search goes on without it
        raise EmbeddingsUnavailable(f"can't load {settings.embeddings_model}: {exc}") from exc


def _vectors(texts: list[str]) -> list[list[float]]:
    return [[float(x) for x in vector] for vector in _model().embed(texts)]


def embed_flows(driver: Driver, database: str) -> int:
    """Embed every flow whose digest changed since it was last embedded. Returns how many."""
    settings = get_settings()
    with driver.session(database=database) as s:
        rows = s.run(
            "MATCH (f:Flow) WHERE f.digest IS NOT NULL "
            "RETURN f.id AS id, f.digest AS digest, f.digest_embedded AS done"
        ).data()
    stale = []
    for row in rows:
        mark = hashlib.sha256(f"{settings.embeddings_model}\n{row['digest']}".encode()).hexdigest()
        if row["done"] != mark:
            stale.append((row["id"], row["digest"], mark))
    if not stale:
        return 0
    dimensions = len(_vectors(["size"])[0])
    with driver.session(database=database) as s:
        s.run(
            f"CREATE VECTOR INDEX {FLOW_INDEX} IF NOT EXISTS FOR (f:Flow) ON f.digest_embedding "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dims, "
            "`vector.similarity_function`: 'cosine'}}",
            dims=dimensions,
        ).consume()
        for start in range(0, len(stale), BATCH):
            part = stale[start : start + BATCH]
            vectors = _vectors([digest for _, digest, _ in part])
            s.run(
                "UNWIND $rows AS row MATCH (f:Flow {id: row.id}) "
                "CALL db.create.setNodeVectorProperty(f, 'digest_embedding', row.vector) "
                "SET f.digest_embedded = row.mark",
                rows=[
                    {"id": i, "mark": m, "vector": v}
                    for (i, _, m), v in zip(part, vectors, strict=True)
                ],
            ).consume()
    return len(stale)


def similar_flows(driver: Driver, database: str, question: str, limit: int) -> list[str]:
    """The flows whose digests are closest in meaning to the question, closest first."""
    vector = _vectors([QUERY_PREFIX + question])[0]
    with driver.session(database=database) as s:
        rows = s.run(
            f"CYPHER 25 MATCH (f:Flow) SEARCH f IN (VECTOR INDEX {FLOW_INDEX} FOR $vector "
            "LIMIT $limit) RETURN f.id AS id",
            limit=limit,
            vector=vector,
        ).data()
    return [r["id"] for r in rows]
