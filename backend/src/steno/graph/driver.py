from functools import lru_cache

from neo4j import Driver, GraphDatabase

from steno.config import get_settings


@lru_cache
def get_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(
        s.neo4j_uri,
        auth=(s.neo4j_user, s.neo4j_password),
        # "Unrecognized" notices (a relationship type no data has yet, e.g. before any topic
        # is produced to) are expected in Steno's queries: not worth a warning per query
        notifications_disabled_classifications=["UNRECOGNIZED"],
    )


def database() -> str:
    return get_settings().neo4j_database
