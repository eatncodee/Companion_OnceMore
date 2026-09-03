"""
Persistent memory pipeline.

Resolution order:
1. Exact structural slot lookup: (subject, canonical predicate).
2. If a slot match exists, ask the LLM judge whether the new fact is a
   duplicate, update, contradiction, or unrelated claim.
3. If no slot match exists, use semantic search only as a fallback for
   extraction/wording drift, then judge the small set of close candidates.

This prevents contradiction detection from depending on embedding distance.
Embeddings widen recall; the structured slot is the primary identity key.
"""

from . import db
from . import vector_store
from . import llm

SEMANTIC_MATCH_THRESHOLD = 0.50
SEMANTIC_CANDIDATE_K = 10
RETRIEVAL_K = 20



def retrieve_memory_block(query: str) -> str:
    """HOT PATH: semantic retrieval -> active SQLite filter."""
    hits = vector_store.search(query, n_results=RETRIEVAL_K)
    active_ids = {f["id"] for f in db.get_active_facts()}
    relevant = [h for h in hits if h["id"] in active_ids][:6]
    if not relevant:
        return "(nothing relevant recalled yet)"
    return "\n".join(f"- {h['text']}" for h in relevant)


def process_turn_memory(user_message: str, turn_number: int):
    """Extract, resolve, and persist memory for one user turn."""
    candidates = llm.extract_facts(user_message, turn_number)

    for cand in candidates:
        cand = _normalize_candidate(cand)
        subject = cand["subject"]
        predicate = cand["predicate"]
        text = cand["text"]

        # PRIMARY GATE: exact canonical slot lookup.
        slot_matches = db.get_active_facts_by_slot(subject, predicate)
        if slot_matches:
            if _resolve_against_matches(cand, slot_matches, turn_number):
                continue
            _insert_fact(cand, turn_number)
            continue

        # FALLBACK GATE: semantic recall for novel phrasing/extraction drift.
        semantic_hits = vector_store.search(text, n_results=SEMANTIC_CANDIDATE_K)
        candidates_to_judge = [
            h for h in semantic_hits
            if h["distance"] < SEMANTIC_MATCH_THRESHOLD
        ]

        resolved = False
        for match in candidates_to_judge:
            old_fact = db.get_fact(match["id"])
            if not old_fact or old_fact["superseded_by"] is not None:
                continue
            verdict = llm.judge_conflict(old_fact["text"], text)
            relationship = verdict.get("relationship")
            if relationship == "duplicate":
                resolved = True
                break
            if relationship in {"update", "contradiction"}:
                _replace_fact(old_fact, cand, turn_number, relationship, verdict)
                resolved = True
                break

        if not resolved:
            _insert_fact(cand, turn_number)


def _resolve_against_matches(cand: dict, old_facts: list, turn_number: int) -> bool:
    """Resolve one new candidate against exact-slot active facts."""
    for old_fact in old_facts:
        verdict = llm.judge_conflict(old_fact["text"], cand["text"])
        relationship = verdict.get("relationship")

        if relationship == "duplicate":
            return True

        if relationship in {"update", "contradiction"}:
            _replace_fact(old_fact, cand, turn_number, relationship, verdict)
            return True

    # Same slot but unrelated means the predicate is being used as a
    # multi-valued bucket (e.g. preference/interest), so keep the new fact.
    return False


def _replace_fact(old_fact: dict, cand: dict, turn_number: int,
                  relationship: str, verdict: dict) -> str:
    new_id = _insert_fact(cand, turn_number)
    reason = f"{relationship}: {verdict.get('reasoning', '')}".strip()
    db.supersede_fact(old_fact["id"], new_id, reason)
    if relationship == "contradiction":
        db.log_contradiction(old_fact["id"], cand["text"], "newest_wins")
    return new_id


def _normalize_candidate(cand: dict) -> dict:
    normalized = dict(cand)
    normalized["subject"] = db.normalize_slot(normalized.get("subject", "user")) or "user"
    normalized["predicate"] = db.normalize_slot(normalized.get("predicate", "other")) or "other"
    normalized["category"] = db.normalize_slot(normalized.get("category", "other")) or "other"
    normalized["object"] = (normalized.get("object") or "").strip()
    normalized["text"] = (normalized.get("text") or "").strip()
    return normalized


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
