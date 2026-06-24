"""Generate balanced Proto ingest shard definitions from a manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz

from proto import PROTO_MANIFEST_PATH, resolve_source


def _pdf_pages(path: str) -> int:
    try:
        doc = fitz.open(resolve_source(path))
        pages = doc.page_count
        doc.close()
        return pages
    except Exception:
        return 0


def estimate_machine(machine: dict, *, count_pages: bool = False) -> dict:
    files = machine.get("files", {})
    pdfs = files.get("pdf", [])
    images = files.get("image", [])
    texts = files.get("text", [])
    pages = sum(_pdf_pages(f["path"]) for f in pdfs) if count_pages else 0
    fallback_pages = len(pdfs) * 100
    weight = max(pages, fallback_pages) + len(images) * 5 + len(texts)
    return {
        "include_path": machine.get("rel_path") or machine["folder"],
        "folder": machine["folder"],
        "customer": machine.get("customer"),
        "slug": machine["slug"],
        "pdfs": len(pdfs),
        "pages": pages,
        "images": len(images),
        "texts": len(texts),
        "weight": weight,
    }


def plan_shards(manifest: dict, *, shard_count: int, count_pages: bool = False) -> dict:
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")

    machines = [estimate_machine(m, count_pages=count_pages) for m in manifest.get("machines", [])]
    shards = [
        {
            "name": f"shard-{i:02d}",
            "include_paths": [],
            "machines": [],
            "pdfs": 0,
            "pages": 0,
            "images": 0,
            "texts": 0,
            "weight": 0,
        }
        for i in range(1, shard_count + 1)
    ]

    for machine in sorted(machines, key=lambda m: m["weight"], reverse=True):
        shard = min(shards, key=lambda s: s["weight"])
        shard["include_paths"].append(machine["include_path"])
        shard["machines"].append(machine)
        shard["pdfs"] += machine["pdfs"]
        shard["pages"] += machine["pages"]
        shard["images"] += machine["images"]
        shard["texts"] += machine["texts"]
        shard["weight"] += machine["weight"]

    return {
        "summary": {
            "shard_count": shard_count,
            "machine_count": len(machines),
            "pdfs": sum(m["pdfs"] for m in machines),
            "pages": sum(m["pages"] for m in machines),
            "images": sum(m["images"] for m in machines),
            "texts": sum(m["texts"] for m in machines),
            "weight": sum(m["weight"] for m in machines),
            "count_pages": count_pages,
        },
        "shards": shards,
    }


def _hcl_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hcl_list(values: list[str], *, indent: int = 6) -> str:
    if not values:
        return "[]"
    pad = " " * indent
    inner = "\n".join(f"{pad}{_hcl_string(value)}," for value in values)
    return "[\n" + inner + "\n" + (" " * (indent - 2)) + "]"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "proto"


def render_terraform_shards(
    plan: dict,
    *,
    name_prefix: str,
    stage_prefix: str | None = None,
    source_prefix: str = "/data/source",
    cache_prefix: str = "/data/cache",
    manifest_prefix: str = "/data/manifest",
    include_assignment: bool = True,
    include_import_jobs: bool = True,
    import_sleep: float = 0.05,
) -> str:
    """Render a Terraform tfvars shard map for staged PDF/text and image ingest."""

    name_prefix = _slug(name_prefix)
    stage_prefix = _slug(stage_prefix or name_prefix)
    source_prefix = source_prefix.rstrip("/")
    cache_prefix = cache_prefix.rstrip("/")
    manifest_prefix = manifest_prefix.rstrip("/")

    lines: list[str] = []
    if include_assignment:
        lines.append("sharepoint_proto_ingest_shards = {")

    base_indent = "  " if include_assignment else ""

    def emit_shard(name: str, attrs: dict):
        lines.append(f"{base_indent}{name} = {{")
        include_paths = attrs.pop("include_paths", None)
        if include_paths is not None:
            lines.append(f"{base_indent}  include_paths = {_hcl_list(include_paths, indent=len(base_indent) + 4)}")
        for key, value in attrs.items():
            if isinstance(value, str):
                lines.append(f"{base_indent}  {key} = {_hcl_string(value)}")
            elif isinstance(value, bool):
                lines.append(f"{base_indent}  {key} = {str(value).lower()}")
            elif isinstance(value, (int, float)):
                lines.append(f"{base_indent}  {key} = {value}")
            else:
                raise TypeError(f"Unsupported Terraform value for {key}: {value!r}")
        lines.append(f"{base_indent}}}")

    for shard in plan["shards"]:
        index = int(shard["name"].rsplit("-", 1)[-1])
        shard_name = f"{name_prefix}{index:02d}"
        include_paths = shard["include_paths"]
        common = {
            "ingest_workers": 1,
            "ingest_img_workers": 1,
            "ingest_machine_workers": 1,
        }
        emit_shard(
            f"{shard_name}-pdf",
            {
                "include_paths": include_paths,
                "proto_source_root": f"{source_prefix}-{shard_name}-pdf",
                "proto_cache_dir": f"{cache_prefix}-{shard_name}-pdf",
                "proto_manifest_path": f"{manifest_prefix}-{shard_name}-pdf.json",
                "sharepoint_delta_state_path": f"{cache_prefix}-{shard_name}-pdf/sharepoint_delta_state.json",
                "ingest_kinds": "pdf,text",
                **common,
                "ingest_stage_output_dir": f"/data/proto-stage/{stage_prefix}-pdf",
                "sharepoint_extensions": ".pdf,.txt",
            },
        )
        emit_shard(
            f"{shard_name}-img",
            {
                "include_paths": include_paths,
                "proto_source_root": f"{source_prefix}-{shard_name}-img",
                "proto_cache_dir": f"{cache_prefix}-{shard_name}-img",
                "proto_manifest_path": f"{manifest_prefix}-{shard_name}-img.json",
                "sharepoint_delta_state_path": f"{cache_prefix}-{shard_name}-img/sharepoint_delta_state.json",
                "ingest_kinds": "image",
                **common,
                "ingest_stage_output_dir": f"/data/proto-stage/{stage_prefix}-image",
                "sharepoint_extensions": ".jpg,.jpeg,.png,.bmp,.gif,.tif,.tiff,.pcx",
            },
        )

    if include_import_jobs:
        import_common = {
            "include_paths": [],
            "ingest_workers": 1,
            "ingest_img_workers": 1,
            "ingest_machine_workers": 1,
            "ingest_import_sleep": import_sleep,
            "skip_mirror": True,
        }
        emit_shard(
            f"{name_prefix}-import-pdf",
            {
                **import_common,
                "proto_source_root": f"{source_prefix}-{name_prefix}-import-pdf",
                "proto_cache_dir": f"{cache_prefix}-{name_prefix}-import-pdf",
                "proto_manifest_path": f"{manifest_prefix}-{name_prefix}-import-pdf.json",
                "sharepoint_delta_state_path": f"{cache_prefix}-{name_prefix}-import-pdf/sharepoint_delta_state.json",
                "ingest_kinds": "pdf,text",
                "ingest_import_output_dir": f"/data/proto-stage/{stage_prefix}-pdf",
                "ingest_import_checkpoint": f"/data/proto-stage/{stage_prefix}-pdf/import_checkpoint.json",
                "sharepoint_extensions": ".pdf,.txt",
            },
        )
        emit_shard(
            f"{name_prefix}-import-img",
            {
                **import_common,
                "proto_source_root": f"{source_prefix}-{name_prefix}-import-img",
                "proto_cache_dir": f"{cache_prefix}-{name_prefix}-import-img",
                "proto_manifest_path": f"{manifest_prefix}-{name_prefix}-import-img.json",
                "sharepoint_delta_state_path": f"{cache_prefix}-{name_prefix}-import-img/sharepoint_delta_state.json",
                "ingest_kinds": "image",
                "ingest_import_output_dir": f"/data/proto-stage/{stage_prefix}-image",
                "ingest_import_checkpoint": f"/data/proto-stage/{stage_prefix}-image/import_checkpoint.json",
                "sharepoint_extensions": ".jpg,.jpeg,.png,.bmp,.gif,.tif,.tiff,.pcx",
            },
        )

    if include_assignment:
        lines.append("}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", default=PROTO_MANIFEST_PATH)
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--count-pages", action="store_true")
    parser.add_argument("--output", help="Write JSON plan to this path instead of stdout")
    parser.add_argument("--terraform", action="store_true", help="Render a Terraform tfvars shard map instead of JSON")
    parser.add_argument("--name-prefix", default="batch", help="Terraform shard job prefix, e.g. clients-a")
    parser.add_argument("--stage-prefix", default=None, help="Stage directory prefix. Defaults to --name-prefix")
    parser.add_argument("--no-import-jobs", action="store_true", help="Do not include single-writer import jobs")
    parser.add_argument("--no-assignment", action="store_true", help="Render only the map body, without sharepoint_proto_ingest_shards = { ... }")
    parser.add_argument("--import-sleep", type=float, default=0.05, help="Seconds to sleep after each staged import record")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest_path).read_text())
    plan = plan_shards(manifest, shard_count=args.shards, count_pages=args.count_pages)
    if args.terraform:
        payload = render_terraform_shards(
            plan,
            name_prefix=args.name_prefix,
            stage_prefix=args.stage_prefix,
            include_assignment=not args.no_assignment,
            include_import_jobs=not args.no_import_jobs,
            import_sleep=args.import_sleep,
        )
    else:
        payload = json.dumps(plan, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload)


if __name__ == "__main__":
    main()
