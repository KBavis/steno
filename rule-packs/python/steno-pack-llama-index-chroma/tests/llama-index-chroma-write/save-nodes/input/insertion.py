from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore


def save_to_chroma(collection, nodes, embed_model):
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    _ = VectorStoreIndex(nodes=nodes, storage_context=storage_context, embed_model=embed_model)


def open_index(collection, embed_model):
    vector_store = ChromaVectorStore(chroma_collection=collection)
    return VectorStoreIndex.from_vector_store(vector_store=vector_store, embed_model=embed_model)   # no insert
