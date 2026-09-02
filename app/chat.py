"""
Entry point. Run: python chat.py

Persistence proof: run it, say a few things, Ctrl+C, run it again — the
companion will still recall what you told it. Nothing is held only in
process memory; data/memory.db and data/chroma/ are the source of truth.
"""

import os
import threading
from dotenv import load_dotenv
from . import db
from . import vector_store
from . import memory_pipeline
from . import persona
from . import llm


def get_turn_number() -> int:
    facts = db.get_active_facts()
    return (max((f["created_turn"] for f in facts), default=0)) + 1


def main():
    os.makedirs("data", exist_ok=True)
    db.init_db()
    vector_store.get_collection()  # ensure collection exists

    persona_block = render_persona_block()
    history = []
    turn = get_turn_number()

    print(f"({persona_block.splitlines()[0]} is here. Ctrl+C to leave.)\n")

    while True:
        try:
            user_message = input("you: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n(session ended — memory persisted to disk)")
            break

        if not user_message:
            continue

        # --- HOT PATH: retrieve -> generate -> show. No LLM judging here. ---
        memory_block = memory_pipeline.retrieve_memory_block(user_message)
        reply = llm.generate_reply(persona_block, memory_block, history, user_message)
        print(f"mira: {reply}\n")

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        # --- COLD PATH: extract/classify/resolve/store, off the critical path ---
        t = threading.Thread(
            target=memory_pipeline.process_turn_memory,
            args=(user_message, turn),
            daemon=True,
        )
        t.start()

        turn += 1


if __name__ == "__main__":
    main()
