# Companion-AI Core Loop

A small CLI AI companion prototype focused on the assessment's primary problem: **persistent memory + long-range personality consistency**.

The companion is **Mira**, a warm, dry, opinionated character with a stable backstory. User memory lives outside the model context, is retrieved selectively, and is updated rather than blindly accumulated.

> **Scope:** This project intentionally focuses on the memory/persona core loop. UI, auth, voice, images, multi-user support, production-scale infrastructure, and latency optimization are out of scope for this assessment.

## Assessment mapping

| Assessment requirement | Implementation |
|---|---|
| Persist across sessions | SQLite-backed facts survive process restart |
| Extract + store memory | Structured LLM extraction with canonical predicates |
| Retrieve relevantly | Chroma semantic retrieval + active-state filtering |
| Update / contradiction handling | Canonical `(subject, predicate)` slots + LLM relationship judge |
| Don't duplicate facts | Explicit `duplicate` outcome |
| Stay in character | Static Mira persona injected separately on every turn |
| Evaluation harness | Planned as the next stage after core-loop stabilization |

The assessment explicitly makes the core loop the primary deliverable and treats evaluation as an optional stretch goal.

## Run it

This repository uses `uv` and exposes a `companion` CLI entry point.

### Install

```bash
uv sync
```

### Configure Gemini

Create `.env` in the project root:

```env
GEMINI_API_KEY=your_api_key_here
```

Then:

```bash
uv run companion
```

On the first run, Chroma may download its local embedding model. It is cached locally; the embedding index does not need a separate embedding API key.

### Verify persistence

Tell Mira a durable fact, exit with `Ctrl+C`, restart, and ask about it again.

Generated state lives in:

```text
data/memory.db

data/chroma/
```

The in-process conversation history is short-term context only; it is **not** the long-term memory source of truth.

---

# Architecture

```text
                         USER MESSAGE
                              │
                              ▼
                    ┌──────────────────┐
                    │ Memory Retriever │
                    └────────┬─────────┘
                             │
                    relevant active facts
                             │
                             ▼
                    ┌──────────────────┐
                    │  Context Builder │
                    │                  │
                    │ Mira persona     │
                    │ + memory block   │
                    │ + recent history │
                    └────────┬─────────┘
                             │
                             ▼
                           Gemini
                             │
                             ▼
                       Mira response
                             │
                             ▼
                    ┌──────────────────┐
                    │ Memory Extractor │
                    └────────┬─────────┘
                             │
                     canonical facts
                             │
                             ▼
                  ┌────────────────────────┐
                  │ Memory Resolution      │
                  │                        │
                  │ 1. exact slot lookup   │
                  │ 2. semantic fallback   │
                  │ 3. relationship judge │
                  └────────────┬───────────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
             duplicate       update      contradiction
                │              │              │
              ignore       supersede     supersede + log
                               │              │
                               └──────┬───────┘
                                      ▼
                              SQLite + Chroma
```

The design deliberately separates four concerns:

**Conversation history** is short-term context for the current chat session.

**SQLite** is the canonical long-term memory state: structured facts, current/superseded state, turn metadata, and contradiction audit records.

**Chroma** is only the semantic retrieval index. A Chroma result is joined back to SQLite and discarded if the fact is no longer active.

**Persona** is application-owned static state. It is never derived from user memories.

---

# Memory model

A memory is represented as:

```text
(subject, predicate, object)
```

For example:

```text
(user, favorite_language, Rust)
(user, learning_topic, distributed systems)
(user, current_location, Mumbai)
```

The extractor uses a small canonical predicate vocabulary such as:

```text
name
current_location
current_job
current_employer
relationship_status
favorite_language
current_project
learning_topic
goal
plan
preference
interest
opinion
relationship
event
other
```

The important design decision is that **slot identity is structural**.

For example:

```text
(user, favorite_language, Python)
(user, favorite_language, Rust)
```

share the same slot. That lets the system detect a state change without depending on embedding distance.

---

# Why the pipeline is layered

The first draft used embedding similarity as the gate for deciding whether a new fact should be compared with an existing fact. That is unreliable for contradictions because two statements can describe the same state with very different wording.

The current system separates three jobs:

### 1. Structural identity resolution

First ask:

> Does an active fact already exist with the same canonical `(subject, predicate)`?

For known slots, this is the primary identity mechanism.

### 2. Semantic recall widening

If no exact slot exists, Chroma finds semantically close candidates. This is a fallback for wording variation or extraction drift rather than the primary contradiction detector.

### 3. Relationship judgment

Only a small candidate set reaches the LLM judge:

```text
duplicate       → keep existing fact
update          → new fact supersedes old fact
contradiction   → new fact supersedes old fact + log
unrelated       → keep both
```

This avoids both extremes: relying entirely on embeddings, or making an LLM compare every new fact against the entire memory store.

---

# Example: Python → Rust

Turn 1:

```text
My favorite programming language is Python.
```

Stored as:

```text
(user, favorite_language, Python)
```

Later:

```text
Actually Rust is my favorite programming language.
```

The exact slot matches:

```text
(user, favorite_language)
```

The relationship judge determines that the value changed, so the database becomes:

```text
Python → superseded
Rust   → active
```

The old row remains for audit/evaluation, but active retrieval exposes only Rust.

This directly addresses the assessment requirement that an updated/contradicted fact should replace or retire the previous state rather than simply coexist with it.

---

# Duplicate vs update vs contradiction

These are intentionally separate outcomes.

**Duplicate**

```text
I am learning distributed systems.
I am currently learning distributed systems.
```

→ same meaning; do not create another memory.

**Update**

```text
I live in Delhi.
I live in Mumbai now.
```

→ same underlying state, new value; supersede the old fact.

**Contradiction**

```text
I hate coffee.
I love coffee.
```

→ incompatible current claims; supersede the old fact and record the event in `contradiction_log`.

**Unrelated**

```text
I like Python.
I like hiking.
```

→ keep both.

The `contradiction_log` is intentionally an audit trail; the prototype uses a simple newest-wins policy rather than adding a clarification dialogue.

---

# Persona consistency

`persona.py` contains Mira's stable:

- backstory
- personality traits
- stated opinions
- speech patterns

User memory is inserted into a **separate memory block**.

Conceptually:

```text
PERSONA
  → who Mira is
  → what Mira believes
  → how Mira speaks

MEMORY
  → what the user told Mira
  → what is currently true
  → what is relevant to this turn
```

This separation is a deliberate guard against user-specific facts becoming accidental persona instructions.

It also gives the evaluation stage explicit invariants to test over 50+ turns, as requested by the assessment.

---

# Storage decisions

### SQLite instead of MongoDB

MongoDB was considered because facts are document-shaped and easy to version. It was dropped because this is a single-user CLI prototype. SQLite provides durable local storage with no database server to configure.

### Chroma for semantic retrieval

Chroma provides a local persistent semantic index with minimal setup. SQLite remains canonical; Chroma answers only **which memories look relevant**.

### No Redis

Redis was considered for caching/session infrastructure in an earlier scale-oriented design. Multi-user support, production infrastructure, and load handling are explicitly out of scope, so it added complexity without helping the core assessment problem.

---

# What was tried and abandoned

### Embedding similarity as the primary conflict gate

**Abandoned.** Contradictory facts can be poorly correlated semantically. Structural slots now provide the primary identity check, with embeddings retained as a fallback.

### LLM comparison against every stored memory

**Abandoned.** This would create unnecessary model calls as memory grows. Exact slot lookup and bounded semantic candidate retrieval reduce the comparison set first.

### Background fire-and-forget memory writes

**Abandoned.** The initial draft wrote memory on a daemon thread after printing the reply. That created a shutdown race where a process could exit before a memory write completed. Since production latency optimization is explicitly out of scope, the current prototype favors durable correctness over that optimization.

### MongoDB + Redis

**Abandoned.** The initial architecture assumed more infrastructure than this assessment needs. The local SQLite + Chroma design is easier to run, inspect, and explain.

### Conversational contradiction clarification

**Deferred.** A production companion might ask the user which conflicting statement is correct. For this prototype, newest-wins keeps the core loop simple while preserving an audit record when a contradiction is explicitly classified.

---

# Known limitations

1. **Decay is not implemented as a general forgetting policy yet.** The schema has `time_bound` / `expires_at` support, but this prototype does not claim a learned decay curve.
2. **Extraction is still model-dependent.** Canonical predicates reduce drift, but an LLM can still extract the wrong fact.
3. **Relationship judgment is model-dependent.** Edge cases between duplicate/update/contradiction/unrelated still need evaluation.
4. **Newest-wins is intentionally simple.** A real product could use confidence, evidence/source weighting, or user confirmation.
5. **The system is single-user.** No auth, billing, multi-user state isolation, or production infrastructure is implemented because those are outside scope.
6. **No evaluation results are included yet.** The core storage model is already instrumented for evaluation through active facts, supersession chains, and the contradiction log.

---

# Evaluation plan

Once the core loop is frozen, the next step is a lightweight evaluation harness built from deliberately challenging conversations.

### Memory recall

Introduce a fact early, add many unrelated turns, then ask for it later.

### Contradiction/update

Introduce a fact, change it much later, and verify the new value is active while the old value is superseded.

### Semantic recall

Use a paraphrased question rather than repeating the original wording and verify the correct memory is still retrieved.

### Persona consistency

Run 50+ turns that repeatedly exercise stable Mira properties such as her tea/coffee preference, literature-teacher background, Gremlin, dry humor, and willingness to push back rather than blindly agree.

### Metrics

Report pass/fail rates, representative failures, and which category is weakest. An LLM-as-judge is a reasonable option for response-level personality evaluation, but its rubric and limitations should be documented.

An optional oracle baseline can compare the system against a strong model given the full memory store, as suggested by the assessment.

---

# Repository layout

```text
Companion/
├── app/
│   ├── chat.py             # CLI entry point + conversation loop
│   ├── db.py               # canonical SQLite fact store
│   ├── llm.py              # reply, extraction, relationship judge
│   ├── memory_pipeline.py  # retrieval + memory resolution orchestration
│   ├── persona.py          # static Mira persona
│   └── vector_store.py     # Chroma semantic retrieval
│
├── data/
│   ├── memory.db           # generated; canonical memory state
│   └── chroma/             # generated; semantic index
│
├── README.md
├── pyproject.toml
└── uv.lock
```

---

# Walkthrough / demo flow

The intended 15–20 minute walkthrough is:

1. Explain the structured-memory + semantic-retrieval split.
2. Give Mira a fact and show it surviving a restart.
3. Show `Python → Rust` and inspect the supersession in SQLite.
4. Show that repeating the same fact does not duplicate it.
5. Ask paraphrased questions and demonstrate selective retrieval.
6. Explain the failed embedding-only approach and why structural slots fix it.
7. Show the evaluation harness and numerical results once those are added.

The assessment specifically asks for the working source, a README covering architecture decisions, abandoned approaches, and limitations, plus the evaluation harness/results if completed.

## Submission philosophy

The project is intentionally small. The goal is not to simulate a production-scale companion stack; it is to make the **memory state explicit, persistent, inspectable, and testable**, and to show the engineering reasoning behind it.