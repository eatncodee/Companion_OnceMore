"""
LLM calls, kept to exactly two shapes:

1. generate_reply()   — the hot path. Persona + retrieved memory -> reply.
2. extract_facts()    — the cold path. One structured-output call that does
                         extraction AND classification together, instead of
                         two separate round trips.
3. judge_conflict()   — cold path, only called when a candidate fact is
                         similar to an existing one. Decides update vs
                         contradiction vs unrelated.

Model choice: Gemini, via the google-genai SDK, using forced function
calling (tool_config mode=ANY) for structured output on extract/judge —
the equivalent of Anthropic's forced tool_choice. Swap MODEL below or the
client construction if you'd rather use a different provider — nothing
else in the pipeline depends on which LLM backs these three functions.
"""

import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-3.5-flash-lite"
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


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
    description="Record memory-worthy facts extracted from the user's message.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "facts": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "subject": {"type": "STRING"},
                        "predicate": {"type": "STRING"},
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

EXTRACT_SYSTEM = """You extract memory-worthy facts from a single user message in an
ongoing conversation with a companion AI.

Only extract facts the user DIRECTLY STATED about themselves, their life, their
opinions, their plans, or their relationships — never something you inferred,
guessed, or that the assistant said. If the message has no such fact (small talk,
a question with no self-disclosure, an off-hand remark with nothing durable in it),
call the tool with an empty facts list. Do not pad with speculative facts."""


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
    description="Judge the relationship between an old fact and a new candidate fact.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "relationship": {
                "type": "STRING",
                "enum": ["same_fact_updated", "contradiction", "unrelated"],
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
        "Is the NEW fact an update of the same underlying fact (e.g. a status "
        "change), a contradiction (both claim to be currently true but conflict), "
        "or unrelated (just semantically similar, not actually connected)?"
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
