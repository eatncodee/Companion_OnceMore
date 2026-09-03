# Companion-AI Core Loop

A small CLI AI companion prototype focused on persistent memory, relevant retrieval,
state updates, contradiction handling, and long-range persona consistency.

The system uses a structured memory store as its source of truth and a semantic
index for recall. The companion persona is kept separate from user memory so that
remembered user facts do not silently change the character's identity or behavior.

---

## Features

- Persistent memory across process restarts
- Structured memory extraction from user messages
- Canonical `(subject, predicate, object)` memory representation
- Exact slot-based memory resolution
- Semantic retrieval as a fallback for wording/extraction variation
- Duplicate detection
- State updates through fact supersession
- Contradiction logging
- Selective memory retrieval instead of injecting the entire store
- Stable, application-owned companion persona
- Manual 50+ turn memory and persona evaluation

---

## Run it

### Requirements

- Python 3.12+
- `uv`
- Gemini API key

### Setup

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_api_key_here
```

Install dependencies:

```bash
uv sync
```

Run:

```bash
uv run companion
```

On first run, Chroma may download its local embedding model. It is cached locally
and does not require a separate vector database server.

The application creates its persistent state under:

```text
data/
├── memory.db
└── chroma/
```

---

## Architecture

```text
                         User message
                              │
                              ▼
                    ┌──────────────────┐
                    │ Memory Retrieval  │
                    │                  │
                    │ Chroma semantic  │
                    │ + SQLite filter  │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Context Builder  │
                    │                  │
                    │ Persona          │
                    │ + relevant       │
                    │   memories       │
                    │ + recent history │
                    └────────┬─────────┘
                             │
                             ▼
                           Gemini
                             │
                             ▼
                      Companion reply
                             │
                             ▼
                    Memory extraction
                             │
                             ▼
                 Canonical fact normalization
                             │
                             ▼
                 Exact slot lookup first
                       /            \
                     found          not found
                       │                │
                       ▼                ▼
                  LLM judge       Semantic fallback
                       │                │
                       └────────┬───────┘
                                ▼
                 duplicate / update / contradiction /
                              unrelated
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
             ignore         supersede          insert
                              old fact
                           + log if needed
```

---

## Memory model

Each durable memory is represented as:

```text
(subject, predicate, object)
```

For example:

```text
(user, favorite_language, Rust)
(user, learning_topic, distributed systems)
(user, current_location, Mumbai)
(user, relationship_status, married)
```

SQLite is the canonical source of truth for these facts.

Each fact also retains metadata such as:

- creation turn
- creation time
- category
- whether it is time-bound
- superseded-by relationship
- supersession reason

Superseded facts are retained for history and auditing, but are not treated as
active memories during retrieval.

---

## Why the memory pipeline is layered

The system deliberately separates three concerns:

### 1. Structural identity

The canonical `(subject, predicate)` pair represents the underlying memory slot.

For example:

```text
(user, favorite_language, Python)
(user, favorite_language, Rust)
```

belong to the same logical slot:

```text
(user, favorite_language)
```

This means updates do not depend on embedding similarity.

### 2. Semantic fallback

When no exact slot exists, Chroma is used to find semantically similar existing
memories.

This provides recall coverage when wording varies or extraction does not map
cleanly to an existing slot.

### 3. Relationship judgment

The LLM judge is only called for a small set of candidate pairs.

It classifies the relationship as:

```text
duplicate
update
contradiction
unrelated
```

This keeps the LLM from comparing every new fact against the entire memory store.

---

## Duplicate, update, and contradiction handling

### Duplicate

Example:

```text
"I am currently learning distributed systems."
"I am learning distributed systems."
```

The second statement represents the same underlying fact.

Result:

```text
duplicate → keep existing fact
```

No new active memory is created.

### Update

Example:

```text
(user, favorite_language, Python)
                 ↓
(user, favorite_language, Rust)
```

The logical slot is unchanged, but its current value has changed.

Result:

```text
Python → superseded
Rust   → active
```

### Contradiction

When two incompatible claims refer to the same underlying state, the newer
claim becomes the active state and the old state is retained as historical data.

Result:

```text
old fact → superseded
new fact → active
contradiction → logged
```

### Unrelated

Semantically similar statements that refer to different underlying facts are
allowed to coexist.

---

## Storage decisions

### SQLite

SQLite is the canonical memory store because structured state needs to be:

- persistent
- queryable
- inspectable
- easy to update transactionally
- independent of the embedding index

SQLite answers:

> What does the system currently believe?

### Chroma

Chroma is used only for semantic retrieval.

Every Chroma entry uses the corresponding SQLite fact ID, allowing the system
to retrieve a candidate semantically and then resolve its authoritative state
through SQLite.

Chroma answers:

> Which memories might be relevant to this message?

### Conversation history

Recent conversation history is kept in the LLM context for short-term coherence.
It is separate from long-term memory.

### Persona

The companion persona is static application-owned state and is injected separately
from user memories.

This prevents a user memory from silently becoming a change to the companion's
personality.

---

## What was tried and abandoned

### Embedding similarity as the primary conflict gate

The initial design relied on embedding distance to determine whether a new fact
was similar enough to an existing fact before asking the LLM to judge it.

This failed for state changes such as:

```text
Python → Rust
Delhi → Mumbai
single → married
```

because contradictory or updated statements do not always produce sufficiently
similar embeddings.

The pipeline was changed so that exact canonical slot lookup is the primary
identity mechanism, with semantic retrieval kept as a fallback.

### LLM comparison against every stored fact

Comparing every new memory against the entire memory store would increase cost
with memory size and introduce unnecessary LLM calls.

The current architecture limits the judge to structurally matched or
semantically close candidate facts.

### Fire-and-forget background memory writes

The first version persisted memory in a daemon background thread after responding.

That improved perceived responsiveness but introduced a correctness risk: the
process could exit before the memory write completed.

The current prototype prioritizes durable state correctness and completes memory
processing before accepting the next input.

---

## Persona design

Mira is defined as a stable application-owned persona with:

- a fixed backstory
- personality traits
- stated opinions
- speech patterns
- stable behavioral constraints

Examples include:

- former high-school literature teacher
- full-time writer
- cat named Gremlin
- prefers tea to coffee
- skeptical of productivity culture
- values quiet and "dead time"
- direct and willing to disagree
- avoids generic assistant phrasing

These properties are kept separate from user memory so they can be evaluated for
drift independently.

---

## Evaluation

A manual evaluation conversation is included at:

```text
tests/conversations/manual_memory_persona_evaluation.md
```

The manual evaluation covers:

- persistence across process restarts
- structured memory extraction
- exact-slot updates
- duplicate handling
- long-range recall
- paraphrased recall
- retrieval of the current state rather than superseded state
- persona consistency
- resistance to generic-assistant prompting
- long-range state updates

The manual run also identified transient conversational information as a
refinement area. Statements such as temporary mood or weather were retained and
could later be recalled.

The manual evaluation is evidence from representative test conversations, not a
statistical benchmark. A more extensive automated evaluation harness could
extend these scenarios with repeatable pass/fail measurements.

---

## Known limitations

- Memory extraction and relationship judgment depend on LLM structured-output
  quality.
- Temporary conversational state does not yet have a full retention/decay policy.
- The prototype is local and single-user by design.
- Chroma and SQLite are both local stores.
---

## Project structure

```text
companion/
├── app/
│   ├── chat.py
│   ├── db.py
│   ├── llm.py
│   ├── memory_pipeline.py
│   ├── persona.py
│   └── vector_store.py
│
├── tests/
│   └── conversations/
│       └── manual_memory_persona_evaluation.md
│
├── data/
│   └── (generated at runtime)
│
├── README.md
├── pyproject.toml
└── uv.lock
```