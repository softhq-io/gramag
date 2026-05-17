"""Rebuild embeddings_normed.npy from existing metadata.pkl (skips PDF/CSV re-ingestion)."""
import os, pickle, time
import numpy as np
from config import INDEX_DIR
from embeddings import generate_embeddings_batch, generate_embedding

BATCH = 100

with open(os.path.join(INDEX_DIR, "metadata.pkl"), "rb") as f:
    metadata = pickle.load(f)
print(f"Chunks to embed: {len(metadata)}")

embeddings = [None] * len(metadata)
t0 = time.time()

for i in range(0, len(metadata), BATCH):
    batch = metadata[i:i+BATCH]
    texts = [c["text"] for c in batch]
    try:
        for j, e in enumerate(generate_embeddings_batch(texts, batch_size=BATCH, delay=0)):
            embeddings[i+j] = e
    except Exception as ex:
        print(f"  Error batch {i}: {ex} — retrying once")
        time.sleep(3)
        try:
            for j, e in enumerate(generate_embeddings_batch(texts, batch_size=BATCH, delay=0)):
                embeddings[i+j] = e
        except Exception as ex2:
            print(f"  FAILED batch {i}: {ex2}")

    if (i // BATCH) % 10 == 0:
        elapsed = time.time() - t0
        done = min(i+BATCH, len(metadata))
        rate = done / elapsed if elapsed else 0
        eta = (len(metadata) - done) / rate if rate else 0
        print(f"  {done}/{len(metadata)}  elapsed={elapsed:.0f}s  eta={eta:.0f}s")
    time.sleep(0.1)

# Find missing and retry individually
missing = [i for i, e in enumerate(embeddings) if e is None]
if missing:
    print(f"Retrying {len(missing)} missing embeddings...")
    for i in missing:
        try:
            embeddings[i] = generate_embedding(metadata[i]["text"])
        except Exception as ex:
            print(f"  Still failed idx {i}: {ex}")

# Filter out anything still missing
good = [(m, e) for m, e in zip(metadata, embeddings) if e is not None]
print(f"Good: {len(good)} / {len(metadata)}")

emb_matrix = np.array([e for _, e in good], dtype=np.float32)
new_metadata = [m for m, _ in good]

# Save raw + normed
np.save(os.path.join(INDEX_DIR, "embeddings.npy"), emb_matrix)
norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
norms[norms == 0] = 1
np.save(os.path.join(INDEX_DIR, "embeddings_normed.npy"), emb_matrix / norms)

# Rewrite metadata if any chunks were dropped
if len(new_metadata) != len(metadata):
    with open(os.path.join(INDEX_DIR, "metadata.pkl"), "wb") as f:
        pickle.dump(new_metadata, f)
    print(f"Metadata rewritten ({len(metadata)} → {len(new_metadata)})")

print(f"Done in {time.time()-t0:.0f}s. Shape: {emb_matrix.shape}")
