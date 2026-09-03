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


def main():
    load_dotenv()
    os.makedirs("data", exist_ok=True)
    db.init_db()
    vector_store.get_collection()  # ensure collection exists

    persona_block = persona.render_persona_block()
    history = []
    memory_jobs = []

    print(f"\n{persona_block.splitlines()[0]} is here...(Ctrl+C to leave)")

    while True:
        try:
            user_message = input("You -> ").strip()
        except (KeyboardInterrupt, EOFError):
            for job in memory_jobs:
                job.join()
            print("\n(session ended — memory persisted to disk)")
            break

        if not user_message:
            continue

        turn = db.next_turn()

        # --- HOT PATH: retrieve -> generate -> show. No LLM judging here. ---
        memory_block = memory_pipeline.retrieve_memory_block(user_message)
        reply = llm.generate_reply(persona_block, memory_block, history, user_message)
        print(f"Mira: {reply}\n")

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        # COLD PATH: extract/judge/store off the input loop so the next prompt is not blocked.
        #  Join leftover jobs on Ctrl+C so the last turn is saved.
        job = threading.Thread(
            target=memory_pipeline.process_turn_memory,
            args=(user_message, turn),
            daemon=True,
        )
        job.start()
        memory_jobs.append(job)


if __name__ == "__main__":
    main()
