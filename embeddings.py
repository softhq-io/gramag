"""Azure OpenAI embedding wrapper for Gramag Knowledge Graph."""

import os
import time
from ai_client import embed_batch, embed_one


MAX_EMBED_CHARS = int(os.getenv("EMBED_MAX_CHARS", "18000"))


def _trim_for_embedding(text: str) -> str:
    if len(text) <= MAX_EMBED_CHARS:
        return text
    return text[:MAX_EMBED_CHARS] + "\n\n[TRUNCATED FOR EMBEDDING]"


def generate_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Generate embedding for a single text."""
    return embed_one(_trim_for_embedding(text), input_type=task_type)


def generate_embeddings_batch(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
    batch_size: int = 100,
    delay: float = 0.1,
) -> list[list[float]]:
    """Generate embeddings in batches."""
    if not texts:
        return []

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = [_trim_for_embedding(text) for text in texts[i:i + batch_size]]
        try:
            all_embeddings.extend(embed_batch(batch, input_type=task_type))
        except Exception as e:
            print(f"  Embed error at batch {i}: {e}")
            time.sleep(2)
            # Retry once
            try:
                all_embeddings.extend(embed_batch(batch, input_type=task_type))
            except Exception:
                for text in batch:
                    try:
                        all_embeddings.append(embed_one(text, input_type=task_type))
                    except Exception as item_error:
                        print(f"  Embed item error at batch {i}: {item_error}")
                        all_embeddings.append([])

        if delay > 0:
            time.sleep(delay)

    return all_embeddings


def generate_query_embedding(text: str) -> list[float]:
    """Generate embedding for a search query."""
    return generate_embedding(text, task_type="RETRIEVAL_QUERY")
