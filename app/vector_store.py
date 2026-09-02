"""
Embedding store for semantic retrieval.

Design decision: Chroma's default local embedding function + persistent
client, so this also needs zero external server / API key just to run
retrieval — only the LLM calls (extraction, conflict-judging, persona
reply) need a real API key. Keeps the "runnable from a README" bar low.

Every entry's Chroma id == the SQLite fact id in db.py. This is the join
key between the two stores: Chroma tells you WHICH facts are semantically
relevant, SQLite tells you whether that fact is still active (not
superseded) and gives you its full structured record. Never trust Chroma's
copy of the text as the source of truth — always resolve back to SQLite.
"""

import chromadb

COLLECTION_NAME = "companion_facts"


def get_collection(path: str = "data/chroma"):
    client = chromadb.PersistentClient(path=path)
    return client.get_or_create_collection(COLLECTION_NAME)


def add_fact(fact_id: str, text: str, category: str, path: str = "data/chroma"):
    col = get_collection(path)
    col.add(ids=[fact_id], documents=[text], metadatas=[{"category": category}])


def search(query: str, n_results: int = 8, path: str = "data/chroma"):
    col = get_collection(path)
    if col.count() == 0:
        return []
    n = min(n_results, col.count())
    res = col.query(query_texts=[query], n_results=n)
    out = []
    for fact_id, text, dist in zip(res["ids"][0], res["documents"][0], res["distances"][0]):
        out.append({"id": fact_id, "text": text, "distance": dist})
    return out
