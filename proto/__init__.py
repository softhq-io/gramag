"""Gramag multimodal prototype — Machine-scoped KB on sample Drive dump."""

import os

PROTO_GRAPH_NAME = os.getenv("PROTO_GRAPH_NAME", "gramag_proto")

# Production (Fly volume): /data/source, /data/cache
# Local dev: user's Downloads + project tree
PROTO_ROOT = os.getenv(
    "PROTO_ROOT",
    "/Users/piotrzwolinski/Downloads/drive-download-20260414T171809Z-3-001",
)
PROTO_CACHE_DIR = os.getenv(
    "PROTO_CACHE_DIR",
    "/Users/piotrzwolinski/projects/gramag/proto/cache",
)
PROTO_MANIFEST_PATH = os.getenv(
    "PROTO_MANIFEST_PATH",
    "/Users/piotrzwolinski/projects/gramag/proto/manifest.json",
)


def resolve_source(rel_or_abs: str) -> str:
    """Map a stored path (relative to PROTO_ROOT, or absolute) to the current root.

    Absolute paths that match the *legacy* root are rewritten; relative paths are
    joined to PROTO_ROOT. Already-correct absolute paths pass through.
    """
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        if os.path.exists(rel_or_abs):
            return rel_or_abs
        # Try to salvage by stripping any known legacy prefix
        for legacy in (
            "/Users/piotrzwolinski/Downloads/drive-download-20260414T171809Z-3-001",
        ):
            if rel_or_abs.startswith(legacy):
                candidate = os.path.join(PROTO_ROOT, rel_or_abs[len(legacy):].lstrip("/"))
                if os.path.exists(candidate):
                    return candidate
        return rel_or_abs  # let caller handle missing
    return os.path.join(PROTO_ROOT, rel_or_abs)


def resolve_cache(rel_or_abs: str) -> str:
    """Resolve cache paths (page PNGs) against PROTO_CACHE_DIR."""
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        if os.path.exists(rel_or_abs):
            return rel_or_abs
        for legacy in (
            "/Users/piotrzwolinski/projects/gramag/proto/cache",
        ):
            if rel_or_abs.startswith(legacy):
                candidate = os.path.join(PROTO_CACHE_DIR, rel_or_abs[len(legacy):].lstrip("/"))
                if os.path.exists(candidate):
                    return candidate
        return rel_or_abs
    return os.path.join(PROTO_CACHE_DIR, rel_or_abs)

SAMPLE_MACHINES = [
    "Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00",
    "Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282",
    "SMB",
]
