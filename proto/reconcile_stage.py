"""Build an immutable, manifest-aligned view of staged Proto records."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from proto.source_fingerprint import source_fingerprint


SUPPORTED_KINDS = {"pdf", "text", "image"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_source_key(record: dict) -> tuple[str, str, str, str]:
    machine = record["machine"]
    source = record["file"]
    return (
        machine["slug"],
        record["kind"],
        source["rel"],
        record["fingerprint"],
    )


def _record_identity(record: dict) -> tuple[str, str]:
    record_type = record["record"]
    if record_type == "document":
        return record_type, ""
    if record_type == "manual_section":
        return record_type, record["section"]["id"]
    if record_type == "config":
        return record_type, record["config"]["id"]
    if record_type == "image":
        return record_type, record["image"]["id"]
    raise RuntimeError(f"unsupported staged record type: {record_type}")


def _record_sort_key(record: dict) -> tuple:
    order = {"document": 0, "manual_section": 1, "config": 1, "image": 1}
    record_type = record["record"]
    if record_type == "manual_section":
        detail = (int(record["section"]["page"]), record["section"]["id"])
    else:
        detail = _record_identity(record)[1:]
    return order[record_type], *detail


def _config(
    input_dir: Path,
    manifest_paths: list[Path],
    kinds: set[str],
    exclude_file_names: list[str],
) -> dict:
    return {
        "version": 1,
        "input_dir": str(input_dir),
        "manifests": [
            {"path": str(path), "sha256": _sha256(path)}
            for path in manifest_paths
        ],
        "kinds": sorted(kinds),
        "exclude_file_names": sorted(exclude_file_names),
    }


def verify_extraction_markers(
    marker_paths: list[Path],
    manifest_paths: list[Path],
) -> dict[str, str]:
    """Require one valid extraction marker for every exact current manifest."""
    current_manifests = {
        str(path.resolve()): _sha256(path.resolve())
        for path in manifest_paths
    }
    verified: dict[str, str] = {}
    for marker_path in marker_paths:
        marker_path = marker_path.resolve()
        if not marker_path.is_file():
            raise RuntimeError(f"required extraction marker is missing: {marker_path}")
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        manifest_value = marker.get("manifest_path")
        if not manifest_value:
            raise RuntimeError(
                f"extraction marker has no manifest path: {marker_path}"
            )
        manifest_path = str(Path(manifest_value).resolve())
        if manifest_path not in current_manifests:
            raise RuntimeError(
                f"extraction marker references an unexpected manifest: "
                f"{marker_path} -> {manifest_path}"
            )
        expected_hash = current_manifests[manifest_path]
        if marker.get("manifest_sha256") != expected_hash:
            raise RuntimeError(
                f"extraction marker manifest hash is stale: {marker_path}"
            )
        if manifest_path in verified:
            raise RuntimeError(
                f"multiple extraction markers reference {manifest_path}"
            )
        verified[manifest_path] = str(marker_path)

    missing = sorted(set(current_manifests) - set(verified))
    if missing:
        raise RuntimeError(
            f"current manifests lack valid extraction markers: {missing}"
        )
    print(
        f"Verified {len(verified)} extraction completion markers",
        flush=True,
    )
    return verified


def reconcile_staged_records(
    input_dir: Path,
    output_dir: Path,
    manifest_paths: list[Path],
    *,
    kinds: set[str],
    exclude_file_names: list[str] | None = None,
) -> dict:
    """Copy only current-manifest staged records into a new immutable directory."""
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    manifest_paths = [path.resolve() for path in manifest_paths]
    exclude_file_names = [name for name in (exclude_file_names or []) if name]
    if input_dir == output_dir:
        raise RuntimeError("reconciled output directory must differ from staged input")
    if not manifest_paths:
        raise RuntimeError("at least one current manifest is required for reconciliation")
    unknown_kinds = kinds - SUPPORTED_KINDS
    if unknown_kinds:
        raise RuntimeError(f"unsupported reconciliation kinds: {sorted(unknown_kinds)}")

    expected_config = _config(
        input_dir,
        manifest_paths,
        kinds,
        exclude_file_names,
    )
    marker_path = output_dir / "_RECONCILIATION.json"
    if output_dir.exists() and any(output_dir.iterdir()):
        if marker_path.is_file():
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
            if existing.get("config") == expected_config:
                print(f"Reusing reconciled staged records from {output_dir}", flush=True)
                return existing
        raise RuntimeError(
            f"reconciled output directory is not empty or has a different manifest: "
            f"{output_dir}"
        )

    allowed: dict[tuple[str, str, str, str], dict] = {}
    exclusion_matches: dict[str, list[tuple[str, str, str]]] = {
        name: [] for name in exclude_file_names
    }
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for machine in manifest["machines"]:
            for kind in kinds:
                for source in machine["files"][kind]:
                    if source.get("name") in exclusion_matches:
                        exclusion_matches[source["name"]].append(
                            (machine["slug"], kind, source["rel"])
                        )
                        continue
                    key = (
                        machine["slug"],
                        kind,
                        source["rel"],
                        source_fingerprint(source),
                    )
                    if key in allowed:
                        raise RuntimeError(f"duplicate current manifest source: {key[:3]}")
                    allowed[key] = source

    for name, matches in exclusion_matches.items():
        if len(matches) != 1:
            raise RuntimeError(
                f"source exclusion {name!r} matched {len(matches)} current files; "
                "expected exactly one"
            )
        machine_slug, kind, rel = matches[0]
        print(
            "Reconciliation exclusion: "
            f"machine={machine_slug} kind={kind} rel={rel}",
            flush=True,
        )

    records_by_source: dict[tuple[str, str, str, str], dict[tuple[str, str], dict]] = (
        defaultdict(dict)
    )
    input_records = 0
    stale_records = 0
    for path in sorted(input_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as source:
            for line_no, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                input_records += 1
                try:
                    record = json.loads(line)
                    source_key = _record_source_key(record)
                    identity = _record_identity(record)
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"invalid staged record {path.name}:{line_no}: {exc}"
                    ) from exc
                if source_key not in allowed:
                    stale_records += 1
                    continue
                records_by_source[source_key][identity] = record

    errors = []
    for source_key in sorted(allowed):
        records = list(records_by_source.get(source_key, {}).values())
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[record["record"]] += 1
        kind = source_key[1]
        if kind == "pdf":
            documents = [
                record for record in records if record["record"] == "document"
            ]
            expected_sections = max(
                (
                    int(record.get("expected_sections") or 0)
                    for record in documents
                ),
                default=0,
            )
            if (
                len(documents) != 1
                or expected_sections <= 0
                or counts["manual_section"] < expected_sections
            ):
                errors.append(
                    f"{source_key[:3]}: document={len(documents)} "
                    f"sections={counts['manual_section']} expected={expected_sections}"
                )
        elif kind == "text" and counts["config"] != 1:
            errors.append(f"{source_key[:3]}: config={counts['config']} expected=1")
        elif kind == "image" and counts["image"] != 1:
            errors.append(f"{source_key[:3]}: image={counts['image']} expected=1")
    if errors:
        preview = "; ".join(errors[:10])
        raise RuntimeError(
            f"reconciliation found {len(errors)} incomplete current sources: {preview}"
        )

    output_records: dict[str, list[dict]] = defaultdict(list)
    for source_key in sorted(records_by_source):
        machine_slug = source_key[0]
        records = sorted(records_by_source[source_key].values(), key=_record_sort_key)
        output_records[machine_slug].extend(records)

    output_dir.mkdir(parents=True, exist_ok=True)
    written_records = 0
    for machine_slug, records in sorted(output_records.items()):
        destination = output_dir / f"{machine_slug}.jsonl"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=output_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                for record in records:
                    target.write(json.dumps(record, ensure_ascii=False) + "\n")
                    written_records += 1
                target.flush()
                os.fsync(target.fileno())
            os.replace(tmp_name, destination)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    report = {
        "config": expected_config,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "current_sources": len(allowed),
        "input_records": input_records,
        "written_records": written_records,
        "stale_or_excluded_records": stale_records,
        "machines": len(output_records),
    }
    marker_content = json.dumps(report, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{marker_path.name}.",
        suffix=".tmp",
        dir=output_dir,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as marker:
            marker.write(marker_content)
            marker.flush()
            os.fsync(marker.fileno())
        os.replace(tmp_name, marker_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    print(
        "Reconciled staged records: "
        f"sources={len(allowed)} input_records={input_records} "
        f"written_records={written_records} stale_or_excluded={stale_records} "
        f"output={output_dir}",
        flush=True,
    )
    return report
