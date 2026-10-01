import chromadb
from chromadb.api import ClientAPI

VECTOR_DB_HOST = "chroma"


def client() -> ClientAPI:
    return chromadb.HttpClient(host=VECTOR_DB_HOST, port=8000)


def search(question_embedding: list[float]):
    collection = client().get_collection("docs")
    return collection.query(query_embeddings=[question_embedding], n_results=5)


def store(ids: list[str], embeddings: list[list[float]]):
    client().get_collection("docs").add(ids=ids, embeddings=embeddings)
