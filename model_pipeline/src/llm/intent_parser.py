"""
Intent Parser — LLM Role 1: Natural Language Understanding.

Parses user queries like "Can I buy the Sony WH-1000XM5?" to extract:
    - intent: purchase_query, general_question, or out_of_scope
    - product_reference: the product name/title the user is asking about
    - user_context: any additional context ("I have some money saved up...")

Uses regex/keyword-based parsing when the MockProvider is active,
and structured LLM generation when a real provider is configured.

Usage:
    from llm.intent_parser import parse_user_input
    from llm.llm_provider import get_provider

    provider = get_provider()
    result = parse_user_input("Can I buy the iPhone 15?", provider)
    # → ParsedIntent(intent="purchase_query", product_reference="iPhone 15", ...)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from llm.llm_provider import LLMProvider, MockProvider
from llm.prompts.intent_prompt import INTENT_EXTRACTION_PROMPT, INTENT_SCHEMA

logger = logging.getLogger(__name__)


@dataclass
class ParsedIntent:
    """Result of parsing a user's natural language input."""
    intent: str             # "purchase_query" | "general_question" | "out_of_scope"
    product_reference: str | None   # Extracted product text, e.g., "Sony WH-1000XM5"
    user_context: str | None        # Extra context the user shared
    confidence: float               # 0.0–1.0 confidence in the parse


# ---------------------------------------------------------------------------
# Purchase-intent keyword patterns
# ---------------------------------------------------------------------------

# Patterns that indicate the user wants to evaluate a purchase.
# Each pattern captures the product reference as a named group.
_PURCHASE_PATTERNS: list[re.Pattern] = [
    # "Can I buy <product>?"
    re.compile(
        r"(?:can|could|may)\s+I\s+(?:buy|purchase|afford|get)\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!]?\s*$",
        re.IGNORECASE,
    ),
    # "Should I buy <product>?"
    re.compile(
        r"should\s+I\s+(?:buy|purchase|get)\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!]?\s*$",
        re.IGNORECASE,
    ),
    # "Is it worth buying <product>?"
    re.compile(
        r"is\s+(?:it|the)\s+(?:worth|okay|ok)\s+(?:to\s+)?(?:buy|purchase|get)(?:ing)?\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!]?\s*$",
        re.IGNORECASE,
    ),
    # "I want to buy <product>"
    re.compile(
        r"I\s+(?:want|need|plan|would\s+like)\s+to\s+(?:buy|purchase|get)\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!]?\s*$",
        re.IGNORECASE,
    ),
    # "I'm thinking of buying <product>"
    re.compile(
        r"(?:I(?:'m|am)\s+)?thinking\s+(?:of|about)\s+(?:buy|purchas|gett)ing\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!,]?\s*$",
        re.IGNORECASE,
    ),
    # "What do you think about buying <product>?"
    re.compile(
        r"what\s+do\s+you\s+think\s+(?:about|of)\s+(?:buy|purchas|gett)ing\s+(?:the\s+|a\s+|an\s+)?(?P<product>.+?)[\?\.\!]?\s*$",
        re.IGNORECASE,
    ),
]

# Context pattern: "I have some money saved up and am thinking of buying <product>"
_CONTEXT_PATTERN = re.compile(
    r"^(?P<context>.+?)(?:and\s+(?:am|I'm)\s+(?:thinking|considering))",
    re.IGNORECASE,
)

# Loose keyword check — fallback for queries that don't match exact patterns
_PURCHASE_KEYWORDS = {
    "buy", "purchase", "afford", "get", "worth", "buying", "purchasing",
}

# Financial question keywords (for general_question classification)
_FINANCE_KEYWORDS = {
    "savings", "budget", "income", "expenses", "financial", "money",
    "debt", "loan", "credit", "finances", "spending",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_user_input(query: str, provider: LLMProvider) -> ParsedIntent:
    """
    Parse a user's natural language query to extract intent and product reference.

    When using MockProvider: uses regex/keyword-based parsing.
    When using a real provider: sends the query to the LLM for structured extraction.
    """
    query = query.strip()
    if not query:
        return ParsedIntent(
            intent="out_of_scope",
            product_reference=None,
            user_context=None,
            confidence=1.0,
        )

    # Use regex-based parsing for mock provider (deterministic, testable)
    if isinstance(provider, MockProvider):
        return _parse_with_regex(query)

    # Use LLM-based parsing for real providers
    return _parse_with_llm(query, provider)


# ---------------------------------------------------------------------------
# Regex-based parsing (MockProvider)
# ---------------------------------------------------------------------------

def _parse_with_regex(query: str) -> ParsedIntent:
    """Regex/keyword-based intent parsing — deterministic, no API calls."""

    # Extract user context if present (e.g., "I have some money saved up and...")
    user_context = None
    context_match = _CONTEXT_PATTERN.search(query)
    if context_match:
        user_context = context_match.group("context").strip().rstrip(",")

    # Try each purchase pattern
    for pattern in _PURCHASE_PATTERNS:
        match = pattern.search(query)
        if match:
            product = match.group("product").strip()
            product = _clean_product_reference(product)
            if product:
                logger.info("Intent parsed: purchase_query, product='%s'", product)
                return ParsedIntent(
                    intent="purchase_query",
                    product_reference=product,
                    user_context=user_context,
                    confidence=0.95,
                )

    # Fallback: check for loose purchase keywords + extract product
    query_words = set(query.lower().split())
    if query_words & _PURCHASE_KEYWORDS:
        product = _extract_product_fallback(query)
        if product:
            logger.info("Intent parsed (fallback): purchase_query, product='%s'", product)
            return ParsedIntent(
                intent="purchase_query",
                product_reference=product,
                user_context=user_context,
                confidence=0.70,
            )

    # Check for general financial question
    if query_words & _FINANCE_KEYWORDS:
        logger.info("Intent parsed: general_question")
        return ParsedIntent(
            intent="general_question",
            product_reference=None,
            user_context=None,
            confidence=0.80,
        )

    # Default: out of scope
    logger.info("Intent parsed: out_of_scope")
    return ParsedIntent(
        intent="out_of_scope",
        product_reference=None,
        user_context=None,
        confidence=0.80,
    )


def _clean_product_reference(product: str) -> str:
    """Clean up extracted product text — remove trailing punctuation, articles, noise."""
    product = product.strip().rstrip("?.!,;:")
    # Remove trailing "what do you think" / "what do you think savvio" etc.
    product = re.sub(
        r",?\s*what\s+do\s+you\s+think.*$", "", product, flags=re.IGNORECASE
    ).strip()
    # Remove trailing savvio mentions
    product = re.sub(r",?\s*savvio\s*$", "", product, flags=re.IGNORECASE).strip()
    return product


def _extract_product_fallback(query: str) -> str | None:
    """
    Fallback product extraction: strip purchase keywords and common words
    to isolate the likely product name.
    """
    # Remove common question/purchase framing words
    noise_words = {
        "can", "could", "should", "may", "would", "i", "you", "think",
        "buy", "purchase", "afford", "get", "buying", "purchasing",
        "getting", "the", "a", "an", "this", "that", "is", "it",
        "worth", "okay", "ok", "do", "want", "need", "to", "of",
        "about", "am", "thinking", "considering", "what", "savvio",
        "please", "me", "my", "have", "some", "money", "saved", "up",
        "and", "for",
    }
    words = query.strip().rstrip("?.!,").split()
    product_words = [w for w in words if w.lower() not in noise_words]
    product = " ".join(product_words).strip()
    return product if len(product) >= 2 else None


# ---------------------------------------------------------------------------
# LLM-based parsing (real providers)
# ---------------------------------------------------------------------------

def _parse_with_llm(query: str, provider: LLMProvider) -> ParsedIntent:
    """Parse user intent using a real LLM provider."""
    prompt = INTENT_EXTRACTION_PROMPT.format(query=query)

    try:
        result = provider.generate_structured(
            system_prompt="You are a precise intent extraction system.",
            user_message=prompt,
            schema=INTENT_SCHEMA,
        )

        intent = result.get("intent", "out_of_scope")
        product_ref = result.get("product_reference")
        user_context = result.get("user_context")

        # Validate intent value
        valid_intents = {"purchase_query", "general_question", "out_of_scope"}
        if intent not in valid_intents:
            logger.warning("LLM returned invalid intent '%s', defaulting to out_of_scope", intent)
            intent = "out_of_scope"

        logger.info("Intent parsed (LLM): %s, product='%s'", intent, product_ref)
        return ParsedIntent(
            intent=intent,
            product_reference=product_ref,
            user_context=user_context,
            confidence=0.90,
        )

    except Exception as e:
        logger.error("LLM intent parsing failed: %s — falling back to regex", e)
        return _parse_with_regex(query)
