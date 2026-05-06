"""Convert JSON index to numpy binary for fast loading."""
import json, numpy as np, os, pickle

INDEX_JSON = "/Users/piotrzwolinski/projects/gramag/index.json"
INDEX_DIR = "/Users/piotrzwolinski/projects/gramag/index"

os.makedirs(INDEX_DIR, exist_ok=True)

print("Loading JSON index...")
with open(INDEX_JSON, "r", encoding="utf-8") as f:
    chunks = json.load(f)

print(f"Total chunks: {len(chunks)}")

# Separate embeddings from metadata
embeddings = []
metadata = []
skipped = 0

for c in chunks:
    emb = c.pop("embedding", [])
    if len(emb) > 0:
        embeddings.append(emb)
        metadata.append(c)
    else:
        skipped += 1

print(f"Valid: {len(metadata)}, Skipped: {skipped}")

# Save embeddings as numpy matrix
emb_matrix = np.array(embeddings, dtype=np.float32)
np.save(os.path.join(INDEX_DIR, "embeddings.npy"), emb_matrix)
print(f"Embeddings: {emb_matrix.shape} → {os.path.getsize(os.path.join(INDEX_DIR, 'embeddings.npy'))/1024/1024:.1f} MB")

# Save metadata as pickle (much smaller without embeddings)
with open(os.path.join(INDEX_DIR, "metadata.pkl"), "wb") as f:
    pickle.dump(metadata, f)
print(f"Metadata: {os.path.getsize(os.path.join(INDEX_DIR, 'metadata.pkl'))/1024/1024:.1f} MB")

# Normalize embeddings for fast cosine similarity (just dot product)
norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
norms[norms == 0] = 1
emb_normed = emb_matrix / norms
np.save(os.path.join(INDEX_DIR, "embeddings_normed.npy"), emb_normed)

print("\nDone! Use index/ directory for fast loading.")
