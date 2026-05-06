"""Gemini embedding wrapper for Gramag Knowledge Graph."""

import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, EMBED_MODEL, EMBED_DIMENSIONS

client = genai.Client(api_key=GEMINI_API_KEY)


def generate_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Generate embedding for a single text."""
    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return list(result.embeddings[0].values)


def generate_embeddings_batch(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
    batch_size: int = 100,
    delay: float = 0.1,
) -> list[list[float]]:
    """Generate embeddings in batches (max 100 per API call)."""
    if not texts:
        return []

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            result = client.models.embed_content(
                model=EMBED_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            all_embeddings.extend([list(e.values) for e in result.embeddings])
        except Exception as e:
            print(f"  Embed error at batch {i}: {e}")
            time.sleep(2)
            # Retry once
            try:
                result = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                all_embeddings.extend([list(e.values) for e in result.embeddings])
            except Exception:
                # Fill with empty embeddings
                all_embeddings.extend([[] for _ in batch])

        if delay > 0:
            time.sleep(delay)

    return all_embeddings


def generate_query_embedding(text: str) -> list[float]:
    """Generate embedding for a search query."""
    return generate_embedding(text, task_type="RETRIEVAL_QUERY")
