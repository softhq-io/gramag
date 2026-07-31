"""Stable fingerprints for mirrored Proto source files."""

from __future__ import annotations

from pathlib import Path


def source_fingerprint(source: dict) -> str:
    """Fingerprint source content enough to notice SharePoint refreshes."""
    size = source.get("size")
    mtime = source.get("mtime")
    if size is None or mtime is None:
        from proto import resolve_source

        try:
            stat = Path(resolve_source(source["path"])).stat()
            size = stat.st_size
            mtime = int(stat.st_mtime)
        except OSError:
            size = size or 0
            mtime = mtime or 0
    return (
        f"{source.get('rel', source.get('path', ''))}|"
        f"{size}|{int(mtime or 0)}"
    )
