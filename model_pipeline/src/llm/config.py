"""
LLM integration configuration for SavVio.

Centralized settings for the LLM layer — provider selection, embedding model,
guardrail thresholds, and prompt versioning. Reads from environment variables
with sensible defaults for local development.
"""

import os


class LLMConfig:
    # ---------------------------------------------------------------------------
    # Provider label (training / MLflow logging only)
    # ---------------------------------------------------------------------------
    # Runtime inference uses `llm.llm_provider.get_provider()` → Vertex AI when
    # VERTEX_PROJECT, GCP_PROJECT_ID, or GOOGLE_CLOUD_PROJECT is set; otherwise mock.
    # This field is for lineage in run_pipeline / prompt_engine — keep it aligned.
    PROVIDER = os.getenv("LLM_PROVIDER", "vertex")

    # ---------------------------------------------------------------------------
    # API Keys (only needed for real providers)
    # ---------------------------------------------------------------------------
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # ---------------------------------------------------------------------------
    # Product Resolution (pgvector similarity search)
    # ---------------------------------------------------------------------------
    # Must match the embedding model used in the data pipeline
    # (data_pipeline/dags/src/database/vector_embed.py)
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    EMBEDDING_DIM = 384
    SIMILARITY_THRESHOLD = float(os.getenv("LLM_SIMILARITY_THRESHOLD", "0.3"))
    TOP_K_PRODUCTS = int(os.getenv("LLM_TOP_K_PRODUCTS", "5"))

    # ---------------------------------------------------------------------------
    # Guardrails
    # ---------------------------------------------------------------------------
    # "code" — Python-level safety checks (default)
    # "nemo" — NVIDIA NeMo Guardrails (requires nemoguardrails package + config)
    GUARDRAILS_PROVIDER = os.getenv("LLM_GUARDRAILS_PROVIDER", "code")
    MAX_RESPONSE_WORDS = int(os.getenv("LLM_MAX_RESPONSE_WORDS", "150"))
    MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))

    # ---------------------------------------------------------------------------
    # Prompt Versioning
    # ---------------------------------------------------------------------------
    INTENT_PROMPT_VERSION = "v1.0"
    RESPONSE_PROMPT_VERSION = "v1.0"
    SYSTEM_PROMPT_VERSION = "v1.0"

    # ---------------------------------------------------------------------------
    # LLM Generation Parameters
    # ---------------------------------------------------------------------------
    TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "300"))
