"""Neo4j labels, constraints, and indexes (docs/knowledge-graph.md §3).

The graph's schema is code and documentation, not data in the graph. Labels are
language-agnostic; language specifics live in rule packs and symbol resolvers.
"""

from neo4j import Driver

# Architecture nodes: what the software does. Search, cards, and routing cover only these.
ARCHITECTURE_LABELS = (
    "Organization",
    "Space",
    "Application",
    "Interface",  # + HttpEndpoint / GrpcMethod / KafkaTopic / Queue / org-defined (D61)
    "Schedule",
    "Flow",
    "Step",
    "Entity",
    "DataStore",  # + Relational / Document / KeyValue / Search / Graph
    "Schema",
    "Table",
    "Column",
    "ExternalSystem",
    "KafkaCluster",
)

# Code nodes: how it's built. Functions aren't nodes: they live in each Flow's trace (D60).
CODE_LABELS = (
    "Repository",
    "Module",  # + Service / Library / Contract / Migrations / Test / Build
    "File",
)

# Carded node types carry this label, so one vector and one full-text index cover them all.
SEARCHABLE = "Searchable"

FULLTEXT_INDEX = "searchable_text"
VECTOR_INDEX = "searchable_card_embedding"


def _statements() -> list[str]:
    stmts = [
        f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in (*ARCHITECTURE_LABELS, *CODE_LABELS)
    ]
    stmts.append(
        f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
        f"FOR (n:{SEARCHABLE}) ON EACH [n.name, n.card]"
    )
    return stmts


def apply_schema(driver: Driver, database: str) -> list[str]:
    """Create constraints and indexes. Idempotent."""
    stmts = _statements()
    with driver.session(database=database) as session:
        for stmt in stmts:
            session.run(stmt).consume()
    return stmts


def create_vector_index(driver: Driver, database: str, dimensions: int) -> None:
    """The card vector index. Not created by default: the embedding model is still Open."""
    stmt = (
        f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS "
        f"FOR (n:{SEARCHABLE}) ON n.card_embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, "
        "`vector.similarity_function`: 'cosine'}}"
    )
    with driver.session(database=database) as session:
        session.run(stmt, dims=dimensions).consume()
