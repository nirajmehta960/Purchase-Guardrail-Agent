"""
Intent extraction prompt — sent to the LLM to parse user queries.

Used by intent_parser.py when a real LLM provider is active.
When the mock provider is active, regex-based parsing is used instead.

NOTE: The inline prompt in intent_parser.py is the actively-used version.
This file is kept as a reference / for structured-generation schemas.
"""

VERSION = "v2.0"

# JSON schema for structured generation (used by generate_structured)
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["purchase_query", "general_question", "out_of_scope"],
        },
        "product_reference": {"type": ["string", "null"]},
        "price_hint": {"type": ["number", "null"]},
        "user_context": {"type": ["string", "null"]},
    },
    "required": ["intent", "product_reference"],
}
