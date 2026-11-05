from neo4j import GraphDatabase

from config import settings

driver = GraphDatabase.driver(settings.graph_uri, auth=None)
