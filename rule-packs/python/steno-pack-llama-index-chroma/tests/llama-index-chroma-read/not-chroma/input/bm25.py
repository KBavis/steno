from llama_index.core import VectorStoreIndex


def memory_retriever(nodes, k: int):
    return VectorStoreIndex(nodes=nodes).as_retriever(similarity_top_k=k)
