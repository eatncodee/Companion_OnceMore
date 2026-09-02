# Companion-AI Core Loop

A CLI companion (persona: Mira) with persistent, cross-session memory and
contradiction handling.

## Run it
```
pip install -r requirements.txt
export GEMINI_API_KEY=...
python chat.py
```

Say a few things, `Ctrl+C` to leave, run `python chat.py` again — Mira will
still recall what you told her. `data/memory.db` (SQLite) and `data/chroma/`
(vector index) are the actual source of truth, not the in-process message
history, which is what proves this is real persistence and not just a long
context window.

## Architecture

```
retrieve → generate reply → [show reply to user]
                                    │
                                    └──▶ extract → conflict-check → store   (background)
```

**Hot path** (blocks the user's next line on screen): embed the message,
pull relevant active facts from Chroma, filter to non-superseded via
SQLite, inject into the persona prompt, generate and print the reply.
No LLM judging happens here — this is why the loop stays responsive.

**Cold path** (fires after the reply is already printed, on a background
thread): one structured-output call extracts + classifies candidate facts
from the turn; each candidate is checked against existing facts via vector
similarity; only genuine near-matches get sent to a second LLM call that
decides update / contradiction / unrelated; the result is written to both
stores. The user never waits on this.

### Why SQLite + Chroma, not MongoDB + Redis

The assignment leaves storage choice open. Two stores were dropped from the
original plan:

- **MongoDB → SQLite.** Functionally the same shape (one row/doc per fact,
  easy to version and supersede) but zero external server dependency —
  matters for a "runnable from a README" CLI deliverable that also has to
  survive a process restart with no setup steps. Mongo would be the right
  call if this needed multi-user or horizontal scale, which is explicitly
  out of scope here.
- **Redis → dropped entirely.** It was in the original plan for session-level
  caching under a multi-user/scale assumption. Since multi-user and scale
  are explicitly out of scope, a single Python list held for the process's
  lifetime does the same job with one less moving part to wire and debug
  under time pressure.

Chroma keeps its default local embedding model (downloads once on first
run, no API key needed for retrieval — only the three LLM calls need one).

### What counts as memory-worthy

Extraction is constrained to facts the user *directly stated* about
themselves — identity, preference, relationship, plan, opinion, event. The
extraction prompt explicitly excludes anything the model inferred or
anything the assistant said. Off-hand remarks and pure questions with no
self-disclosure yield zero facts, not padded guesses.

### Conflict policy: newest wins, but logged

When two facts are judged a genuine contradiction (not just an update of
the same underlying fact), the newest one wins and supersedes the old one
— but it's also written to `contradiction_log` in SQLite. An interactive
"which one is right?" clarification flow was considered and deliberately
cut: it adds a conversational branch that's out of scope for an 18-hour
build, and the log gives equivalent information for later eval work
without the added interaction surface.

### Persona/memory separation

`persona.py` and the per-turn memory block are injected as two clearly
separate blocks in the system prompt, never merged into one freeform
string. This is a deliberate guard against extracted facts about the user
quietly turning into instructions about how the companion behaves.

## What was tried and abandoned

- **MongoDB + Redis** as originally planned — replaced per above once the
  scope (single-user CLI, no latency/infra requirement) made them
  unnecessary weight rather than useful infrastructure.
- **Separate LLM calls for extraction and classification** — collapsed
  into one structured-output tool call; splitting them cost an extra
  round trip for no accuracy benefit at this scale.
- **Synchronous memory writes in the hot path** — moved to a background
  thread after confirming the assignment explicitly excludes latency
  optimization as a goal, but a multi-second pause before every reply
  still isn't a companion experience worth shipping even for a demo.

## Known limitations

- Conflict judging only fires when a new fact is semantically close
  (distance < `SIMILARITY_THRESHOLD`) to an existing one in Chroma. A
  contradiction phrased in a very different way from the original fact
  could slip past the similarity gate and get stored alongside the old
  fact rather than resolved against it.
- "Newest wins" for contradictions is a simplification — a real product
  would likely want to surface ambiguous contradictions to the user
  rather than silently picking a side.
- No decay/forgetting curve: facts don't age out on their own except via
  the `time_bound` / `expires_at` fields, which nothing currently sets
  automatically — a fact tagged time-bound today has no code path that
  actually expires it yet.
- No eval harness included — out of time budget for this pass. The
  `contradiction_log` table and `superseded_by` chain in SQLite are built
  specifically so a harness could be added later without changing the
  storage layer: query contradiction_log for caught contradictions, or
  walk superseded_by chains to check a fact was correctly updated rather
  than duplicated.

## Files

- `persona.py` — static persona definition
- `db.py` — SQLite fact store (canonical, versioned)
- `vector_store.py` — Chroma wrapper for semantic retrieval
- `llm.py` — the three LLM call shapes (reply / extract / judge)
- `memory_pipeline.py` — hot path retrieval + cold path extract/resolve/store
- `chat.py` — CLI entry point
- 