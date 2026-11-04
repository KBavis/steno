from neo4j import GraphDatabase

driver = GraphDatabase.driver("neo4j://graph.local:7687", auth=("neo4j", "secret"))
