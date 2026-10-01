from llama_index.core import VectorStoreIndex


def in_memory(nodes):
    return VectorStoreIndex(nodes=nodes)      # LlamaIndex's in-memory store, not Chroma
