"""
Product Resolver — pgvector similarity search for product identification.

Resolves a text product reference (e.g., "Sony WH-1000XM5") to an actual
product_id in the database by embedding the query text and searching the
product_embeddings table via cosine similarity.

Uses the same embedding model (all-MiniLM-L6-v2, 384-dim) as the data pipeline
(data_pipeline/dags/src/database/vector_embed.py) to ensure embedding space
consistency.

Usage:
    from llm.product_resolver import resolve_product
    from savviocore.database.db_connection import get_engine

    engine = get_engine()
    match = resolve_product("Sony WH-1000XM5", engine)
    if match:
        print(f"Found: {match.product_name} (${match.price}), similarity={match.similarity_score:.3f}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

from llm.config import LLMConfig

logger = logging.getLogger(__name__)

# Singleton for the embedding model — loaded once, reused.
_embedding_model = None


def _get_embedding_model():
    """Load the sentence-transformer model once (lazy singleton)."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", LLMConfig.EMBEDDING_MODEL)
        _embedding_model = SentenceTransformer(LLMConfig.EMBEDDING_MODEL)
    return _embedding_model


@dataclass
class ProductMatch:
    """A product matched via vector similarity search."""
    product_id: str
    product_name: str
    price: float
    category: str
    similarity_score: float


def resolve_product(
    product_text: str,
    engine,
    top_k: int | None = None,
    threshold: float | None = None,
) -> ProductMatch | None:
    """
    Resolve a text product reference to an actual product_id via embedding search.

    Args:
        product_text: The product name/description extracted from the user query.
        engine: SQLAlchemy engine connected to the SavVio database.
        top_k: Number of candidate matches to retrieve (default: LLMConfig.TOP_K_PRODUCTS).
        threshold: Minimum cosine similarity to accept (default: LLMConfig.SIMILARITY_THRESHOLD).

    Returns:
        ProductMatch if a product is found above the threshold, else None.
    """
    if not product_text or not product_text.strip():
        logger.warning("Empty product text — cannot resolve.")
        return None

    top_k = top_k or LLMConfig.TOP_K_PRODUCTS
    threshold = threshold if threshold is not None else LLMConfig.SIMILARITY_THRESHOLD

    # Step 1: embed the query
    model = _get_embedding_model()
    query_embedding = model.encode(
        product_text,
        normalize_embeddings=True,
    ).tolist()

    # Step 2: pgvector cosine similarity search
    sql = text("""
        SELECT pe.product_id,
               p.product_name,
               p.price,
               p.category,
               1 - (pe.embedding <=> CAST(:query_embedding AS vector)) AS similarity
        FROM product_embeddings pe
        JOIN products p ON pe.product_id = p.product_id
        ORDER BY pe.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :top_k
    """)

    embedding_str = str(query_embedding)

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sql, {"query_embedding": embedding_str, "top_k": top_k}
            ).fetchall()
    except Exception as e:
        logger.error("Product search query failed: %s", e)
        return None

    if not rows:
        logger.info("No products found for query: '%s'", product_text)
        return None

    # Step 3: check against threshold
    best = rows[0]
    similarity = float(best[4])

    if similarity < threshold:
        logger.info(
            "Best match '%s' (similarity=%.3f) below threshold %.3f",
            best[1], similarity, threshold,
        )
        return None

    match = ProductMatch(
        product_id=str(best[0]),
        product_name=str(best[1]),
        price=float(best[2]),
        category=str(best[3]) if best[3] else "Unknown",
        similarity_score=similarity,
    )

    logger.info(
        "Product resolved: '%s' → '%s' (id=%s, similarity=%.3f)",
        product_text, match.product_name, match.product_id, match.similarity_score,
    )
    return match


def resolve_product_by_id(product_id: str, engine) -> ProductMatch | None:
    """
    Direct DB lookup when the caller already has a product_id (no vector search).

    Used when the API receives an explicit product_id in the request.
    """
    sql = text("""
        SELECT product_id, product_name, price, category
        FROM products
        WHERE product_id = :product_id
    """)

    try:
        with engine.connect() as conn:
            row = conn.execute(sql, {"product_id": product_id}).fetchone()
    except Exception as e:
        logger.error("Product lookup failed for id=%s: %s", product_id, e)
        return None

    if not row:
        logger.info("No product found for id=%s", product_id)
        return None

    return ProductMatch(
        product_id=str(row[0]),
        product_name=str(row[1]),
        price=float(row[2]),
        category=str(row[3]) if row[3] else "Unknown",
        similarity_score=1.0,  # exact match
    )
