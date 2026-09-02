"""
Orchestrates the two paths described in the README:

- retrieve_memory_block()  -> HOT PATH. Called before every reply. Must be
  cheap: one embedding search + a filter against SQLite. No LLM judging here.

- process_turn_memory()    -> COLD PATH. Called AFTER the reply is already
  shown to the user (fire-and-forget in a background thread from chat.py).
  Does extraction, similarity search, conflict judging, and writes.

Similarity threshold (SIMILARITY_THRESHOLD) decides when two facts are
"close enough" to send to the conflict judge at all — most new facts won't
be close to anything and skip straight to a plain insert, which keeps the
average cold-path cost low (extraction call only, no judge call).
"""

import db
import vector_store
import llm

SIMILARITY_THRESHOLD = 0.35  # chroma cosine distance; lower = more similar
RETRIEVAL_K = 6


def retrieve_memory_block(query: str) -> str:
    """HOT PATH. Returns a formatted string to inject into the system prompt."""
    hits = vector_store.search(query, n_results=RETRIEVAL_K)
    active_ids = {f["id"] for f in db.get_active_facts()}

    relevant = [h for h in hits if h["id"] in active_ids]
    if not relevant:
        return "(nothing relevant recalled yet)"

    return "\n".join(f"- {h['text']}" for h in relevant)


def process_turn_memory(user_message: str, turn_number: int):
    """COLD PATH. Extract, resolve conflicts, persist. Safe to run off-thread."""
    candidates = llm.extract_facts(user_message, turn_number)

    for cand in candidates:
        text = cand["text"]
        similar = vector_store.search(text, n_results=3)
        close_matches = [s for s in similar if s["distance"] < SIMILARITY_THRESHOLD]

        resolved = False
        for match in close_matches:
            old_fact = db.get_fact(match["id"])
            if not old_fact or old_fact["superseded_by"] is not None:
                continue  # already stale, ignore

            verdict = llm.judge_conflict(old_fact["text"], text)
            relationship = verdict.get("relationship")

            if relationship == "same_fact_updated":
                new_id = _insert_fact(cand, turn_number)
                db.supersede_fact(old_fact["id"], new_id, "updated: " + verdict.get("reasoning", ""))
                resolved = True
                break

            if relationship == "contradiction":
                # Policy: newest wins, but log it — see README "Conflict policy".
                new_id = _insert_fact(cand, turn_number)
                db.supersede_fact(old_fact["id"], new_id, "contradiction: " + verdict.get("reasoning", ""))
                db.log_contradiction(old_fact["id"], text, "newest_wins")
                resolved = True
                break

            # relationship == "unrelated" -> fall through, try next match / plain insert

        if not resolved:
            _insert_fact(cand, turn_number)


def _insert_fact(cand: dict, turn_number: int) -> str:
    fact_id = db.save_fact(
        subject=cand["subject"],
        predicate=cand["predicate"],
        obj=cand["object"],
        text=cand["text"],
        category=cand["category"],
        created_turn=turn_number,
        time_bound=cand.get("time_bound", False),
    )
    vector_store.add_fact(fact_id, cand["text"], cand["category"])
    return fact_id
