"""
LLM calls for the companion.

The memory extractor emits canonical predicates so the database can resolve
most updates deterministically by (subject, predicate). Semantic retrieval is
kept as a fallback for wording/extraction drift, and the judge is called only
for candidate pairs that need a relationship decision.
"""

import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-3.5-flash-lite"
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

CANONICAL_PREDICATES = [
    "name",
    "current_location",
    "current_job",
    "current_employer",
    "relationship_status",
    "favorite_language",
    "current_project",
    "learning_topic",
    "goal",
    "plan",
    "preference",
    "interest",
    "opinion",
    "relationship",
    "event",
    "other",
]


def generate_reply(persona_block: str, memory_block: str, history: list, user_message: str) -> str:
    system = f"{persona_block}\n\n---\nThings you remember about the user:\n{memory_block}"

    contents = []
    for turn in history:
        role = "model" if turn["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    resp = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=400,
        ),
    )
    return resp.text


EXTRACT_TOOL = types.FunctionDeclaration(
    name="record_facts",
    description="Record only memory-worthy facts directly stated by the user.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "facts": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "subject": {
                            "type": "STRING",
                            "description": "Canonical subject. Use 'user' for facts about the speaker.",
                        },
                        "predicate": {
                            "type": "STRING",
                            "enum": CANONICAL_PREDICATES,
                        },
                        "object": {"type": "STRING"},
                        "text": {"type": "STRING", "description": "one-line natural language fact"},
                        "category": {
                            "type": "STRING",
                            "enum": ["identity", "preference", "relationship", "plan",
                                     "opinion", "event", "other"],
                        },
                        "time_bound": {"type": "BOOLEAN"},
                    },
                    "required": ["subject", "predicate", "object", "text", "category", "time_bound"],
                },
            }
        },
        "required": ["facts"],
    },
)

EXTRACT_SYSTEM = f"""You extract durable memory from one user message.

Only extract facts the user DIRECTLY stated about themselves, their life,
opinions, plans, or relationships. Never infer facts from questions, context,
or assistant messages. If there is nothing worth storing, return an empty list.

Use exactly one canonical predicate from this set:
{', '.join(CANONICAL_PREDICATES)}

Canonicalization rules:
- Facts about the speaker use subject='user'.
- 'favorite programming language', 'favourite language', etc. -> predicate='favorite_language'.
- 'what I am learning' / 'currently learning' -> predicate='learning_topic'.
- Current states such as where the user lives -> predicate='current_location'.
- Current job/title -> predicate='current_job'.
- Employer/company -> predicate='current_employer'.
- A relationship status such as single/married -> predicate='relationship_status'.
- A concrete current project -> predicate='current_project'.
- Use 'preference', 'interest', and 'opinion' for potentially multi-valued information.

Keep object concise. Keep text faithful to what the user said. Do not invent dates.
Set time_bound=true only for facts that are explicitly temporary or naturally expire;
otherwise false."""


def extract_facts(user_message: str, turn_number: int) -> list:
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=user_message)])],
        config=types.GenerateContentConfig(
            system_instruction=EXTRACT_SYSTEM,
            tools=[types.Tool(function_declarations=[EXTRACT_TOOL])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=["record_facts"]
                )
            ),
        ),
    )
    call = _get_function_call(resp)
    if call:
        facts = call.args.get("facts", [])
        for f in facts:
            f["created_turn"] = turn_number
        return facts
    return []


JUDGE_TOOL = types.FunctionDeclaration(
    name="judge",
    description="Classify the relationship between two candidate facts.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "relationship": {
                "type": "STRING",
                "enum": ["duplicate", "update", "contradiction", "unrelated"],
            },
            "reasoning": {"type": "STRING"},
        },
        "required": ["relationship", "reasoning"],
    },
)


def judge_conflict(old_fact_text: str, new_fact_text: str) -> dict:
    prompt = (
        f"OLD fact: \"{old_fact_text}\"\n"
        f"NEW fact: \"{new_fact_text}\"\n\n"
        "Classify the relationship:\n"
        "- duplicate: same underlying claim, no meaningful change; keep the old row.\n"
        "- update: same underlying attribute, but the value/status has changed; new replaces old.\n"
        "- contradiction: the claims are incompatible as current truths; new replaces old and must be logged.\n"
        "- unrelated: semantically similar wording but different facts; keep both.\n"
        "Prefer duplicate when the meaning is essentially identical."
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        config=types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[JUDGE_TOOL])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=["judge"]
                )
            ),
        ),
    )
    call = _get_function_call(resp)
    if call:
        return dict(call.args)
    return {"relationship": "unrelated", "reasoning": "judge call failed"}


def _get_function_call(resp):
    """Pull the first function call out of a Gemini response, or None."""
    if not resp.candidates:
        return None
    for part in resp.candidates[0].content.parts:
        if part.function_call:
            return part.function_call
    return None
