from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore


def vector_retriever(collection, embedding, k: int):
    vector_store = ChromaVectorStore(chroma_collection=collection)
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store, embed_model=embedding)
    return index.as_retriever(similarity_top_k=k)
