"""
Gramag Knowledge Assistant — Mini PoC
Ask questions about machines, spare parts, and service manuals.

Usage: python3 chat.py
"""
import numpy as np
import pickle, os, sys
from google import genai
from google.genai import types

GEMINI_API_KEY = "AIzaSyAhTT1Pt6mFwEqNfBnJui7zt0ovjZs5p88"
client = genai.Client(api_key=GEMINI_API_KEY)
EMBED_MODEL = "gemini-embedding-001"
CHAT_MODEL = "gemini-2.0-flash"
INDEX_DIR = "/Users/piotrzwolinski/projects/gramag/index"


def load_index():
    print("Loading index...", end=" ", flush=True)
    emb = np.load(os.path.join(INDEX_DIR, "embeddings_normed.npy"))
    with open(os.path.join(INDEX_DIR, "metadata.pkl"), "rb") as f:
        meta = pickle.load(f)
    print(f"{len(meta)} chunks ready.")
    return emb, meta


def search(query: str, emb_matrix, metadata, top_k: int = 15):
    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    q = np.array(result.embeddings[0].values, dtype=np.float32)
    q = q / np.linalg.norm(q)

    scores = emb_matrix @ q  # Fast dot product
    top_idx = np.argsort(scores)[-top_k:][::-1]

    results = []
    for idx in top_idx:
        results.append((float(scores[idx]), metadata[idx]))
    return results


def ask(query: str, emb_matrix, metadata):
    results = search(query, emb_matrix, metadata)

    context_parts = []
    sources = []
    for i, (score, chunk) in enumerate(results):
        src = chunk.get("source", "?")
        ctype = chunk.get("type", "?")
        supplier = chunk.get("supplier", "")
        text = chunk["text"][:600]
        label = f"[{i+1}] ({ctype}) {src}"
        if supplier:
            label += f" [{supplier}]"
        context_parts.append(f"{label}\n{text}")
        sources.append(f"[{i+1}] {src} (sim: {score:.3f})")

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""You are a technical assistant for Gramag Grafische Maschinen AG, a Swiss company that services printing, folding, cutting, enveloping, and labelling machines.

You have access to their ERP data (machines, spare parts, service history) and supplier technical manuals (Avery, Baumer hhs, BDT, Beck, Allen Bradley, etc.).

RULES:
- Answer based ONLY on the provided context. Do not invent information.
- If context is insufficient, say what you found and what's missing.
- Reference sources by number [1], [2], etc.
- Answer in the same language as the question (German or English).
- Be concise and practical — this is for service technicians.
- Include part numbers when available.

CONTEXT:
{context}

QUESTION: {query}"""

    response = client.models.generate_content(
        model=CHAT_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=2000),
    )
    return response.text, sources


def main():
    print("\n" + "=" * 60)
    print("  GRAMAG Knowledge Assistant — Mini PoC")
    print("=" * 60)

    emb_matrix, metadata = load_index()

    type_counts = {}
    for c in metadata:
        t = c.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"\n{' | '.join(f'{t}: {n}' for t, n in sorted(type_counts.items()))}")
    print(f"\nType 'quit' to exit, 'sources' for last sources.\n")

    last_sources = []

    while True:
        try:
            query = input("\033[1;36mFrage> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            break
        if query.lower() == "sources":
            for s in last_sources:
                print(f"  {s}")
            print()
            continue

        try:
            answer, sources = ask(query, emb_matrix, metadata)
            last_sources = sources
            print(f"\n\033[1;32m{answer}\033[0m\n")
            print("\033[0;90m" + " | ".join(s.split("(sim")[0].strip() for s in sources[:5]) + "\033[0m\n")
        except Exception as e:
            print(f"\n  Error: {e}\n")


if __name__ == "__main__":
    main()
