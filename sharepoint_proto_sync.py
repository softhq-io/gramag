"""Mirror SharePoint document-library changes into the Proto KB source tree.

This job uses Microsoft Graph app-only auth. It does not ingest into FalkorDB by
default; it updates PROTO_ROOT and rebuilds the Proto manifest so
``python -m proto.ingest --all`` can process only changed files.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from proto import PROTO_CACHE_DIR, PROTO_MANIFEST_PATH, PROTO_ROOT
from proto.scan import build_manifest, parse_machine_name


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TOKEN_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff",
    ".pcx", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".dwg", ".dxf",
    ".htm", ".html", ".xml",
}


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def required(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def quote_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.strip("/").split("/") if part)


def graph_url(path_or_url: str) -> str:
    if path_or_url.startswith("https://"):
        return path_or_url
    return f"{GRAPH_ROOT}{path_or_url}"


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


@dataclass
class SharePointUrlParts:
    hostname: str
    site_path: str
    drive_name: str | None = None
    root_path: str = ""


def parse_sharepoint_web_url(url: str) -> SharePointUrlParts:
    """Extract Graph addressing hints from a SharePoint browser URL."""
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("SharePoint URL is missing a hostname.")
    query = parse_qs(parsed.query)
    selected_path = query.get("id", [None])[0]
    decoded_id = unquote(selected_path or "")
    path_parts = [part for part in decoded_id.strip("/").split("/") if part]

    if len(path_parts) >= 3 and path_parts[0].lower() == "sites":
        site_path = "/" + "/".join(path_parts[:2])
        drive_name = path_parts[2]
        root_path = "/".join(path_parts[3:])
        return SharePointUrlParts(parsed.hostname, site_path, drive_name, root_path)

    browser_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(browser_parts) >= 2 and browser_parts[0].lower() == "sites":
        site_path = "/" + "/".join(browser_parts[:2])
        return SharePointUrlParts(parsed.hostname, site_path)

    raise ValueError("SharePoint URL does not look like a /sites/... URL.")


def decode_error(err: HTTPError) -> str:
    try:
        body = err.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"{err.code} {err.reason}: {body[:500]}"


class GraphClient:
    def __init__(self, token: str, timeout: int = 60):
        self.token = token
        self.timeout = timeout
        self.context = ssl_context()

    @classmethod
    def from_client_credentials(
        cls,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> "GraphClient":
        body = urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": TOKEN_SCOPE,
            "grant_type": "client_credentials",
        }).encode()
        req = Request(
            f"https://login.microsoftonline.com/{quote(tenant_id, safe='')}/oauth2/v2.0/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30, context=ssl_context()) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            raise RuntimeError(f"Token request failed: {decode_error(err)}") from err
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Token request succeeded but did not return access_token.")
        return cls(token)

    def request_json(self, path_or_url: str) -> dict[str, Any]:
        data = self._request("GET", path_or_url)
        return json.loads(data.decode("utf-8"))

    def request_bytes(self, path_or_url: str) -> bytes:
        return self._request("GET", path_or_url)

    def _request(self, method: str, path_or_url: str, retries: int = 5) -> bytes:
        url = graph_url(path_or_url)
        for attempt in range(retries):
            req = Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                method=method,
            )
            try:
                with urlopen(req, timeout=self.timeout, context=self.context) as resp:
                    return resp.read()
            except HTTPError as err:
                retry_after = err.headers.get("Retry-After")
                if err.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    delay = int(retry_after or min(60, 2 ** attempt))
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Graph request failed for {url}: {decode_error(err)}") from err
        raise RuntimeError(f"Graph request failed for {url}")


@dataclass
class SyncState:
    delta_link: str | None = None
    drive_id: str | None = None
    root_item_id: str | None = None
    root_path: str = ""
    include_paths: list[str] = field(default_factory=list)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(
            delta_link=data.get("delta_link"),
            drive_id=data.get("drive_id"),
            root_item_id=data.get("root_item_id"),
            root_path=data.get("root_path", ""),
            include_paths=data.get("include_paths", []),
            items=data.get("items", {}),
        )

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "delta_link": self.delta_link,
            "drive_id": self.drive_id,
            "root_item_id": self.root_item_id,
            "root_path": self.root_path,
            "include_paths": self.include_paths,
            "items": self.items,
        }, indent=2, ensure_ascii=False))

    def matches_scope(
        self,
        drive_id: str,
        root_item_id: str | None,
        root_path: str,
        include_paths: list[str] | None = None,
    ) -> bool:
        normalized_includes = sorted(include_paths or [])
        return (
            self.drive_id == drive_id
            and self.root_item_id == root_item_id
            and self.root_path.strip("/") == root_path.strip("/")
            and sorted(self.include_paths or []) == normalized_includes
        )


def resolve_site_id(client: GraphClient, site_id: str | None, hostname: str | None, site_path: str | None) -> str:
    if site_id:
        return site_id
    host = required(hostname, "SHAREPOINT_SITE_HOSTNAME")
    path = (site_path or "/").strip("/")
    endpoint = f"/sites/{quote(host, safe='')}:/{quote_path(path)}" if path else f"/sites/{quote(host, safe='')}:/"
    site = client.request_json(endpoint)
    return required(site.get("id"), "SharePoint site id")


def resolve_drive_id(client: GraphClient, site_id: str, drive_id: str | None, drive_name: str | None) -> str:
    if drive_id:
        return drive_id
    if not drive_name:
        drive = client.request_json(f"/sites/{quote(site_id, safe='')}/drive")
        return required(drive.get("id"), "SharePoint drive id")

    drives = client.request_json(f"/sites/{quote(site_id, safe='')}/drives").get("value", [])
    for drive in drives:
        name = drive.get("name", "").lower()
        web_segment = unquote((drive.get("webUrl", "").rstrip("/").rsplit("/", 1)[-1])).lower()
        if drive_name.lower() in (name, web_segment):
            return required(drive.get("id"), f"SharePoint drive id for {drive_name}")
    names = ", ".join(sorted(d.get("name", "?") for d in drives))
    raise SystemExit(f"Drive named {drive_name!r} was not found. Available drives: {names}")


def resolve_root_item_id(client: GraphClient, drive_id: str, root_path: str) -> str | None:
    if not root_path.strip("/"):
        return None
    item = client.request_json(f"/drives/{quote(drive_id, safe='')}/root:/{quote_path(root_path)}")
    return required(item.get("id"), f"SharePoint root item id for {root_path}")


def list_children(client: GraphClient, drive_id: str, root_item_id: str | None) -> list[dict[str, Any]]:
    if root_item_id:
        next_url = f"/drives/{quote(drive_id, safe='')}/items/{quote(root_item_id, safe='')}/children?$top=200"
    else:
        next_url = f"/drives/{quote(drive_id, safe='')}/root/children?$top=200"
    children = []
    while next_url:
        payload = client.request_json(next_url)
        children.extend(payload.get("value", []))
        next_url = payload.get("@odata.nextLink")
    return children


def machine_name_score(name: str) -> bool:
    parsed = parse_machine_name(name)
    raw = parsed.get("raw") or ""
    return bool(parsed.get("model") or parsed.get("serial") or "Nr " in raw or "   " in raw)


def inspect_root_fit(client: GraphClient, drive_id: str, root_path: str) -> dict[str, Any]:
    root_item_id = resolve_root_item_id(client, drive_id, root_path)
    children = list_children(client, drive_id, root_item_id)
    folders = [child for child in children if "folder" in child]
    files = [child for child in children if "file" in child]
    machine_like = [child["name"] for child in folders if machine_name_score(child.get("name", ""))]
    folder_names = [child.get("name", "") for child in folders]
    file_names = [child.get("name", "") for child in files]
    return {
        "root_path": root_path,
        "folder_count": len(folders),
        "file_count": len(files),
        "machine_like_count": len(machine_like),
        "folder_names": folder_names[:20],
        "file_names": file_names[:20],
        "machine_like_names": machine_like[:20],
    }


def print_fit_report(reports: list[dict[str, Any]]):
    for report in reports:
        print()
        print(f"Root: {report['root_path'] or '/'}")
        print(
            f"  folders={report['folder_count']} files={report['file_count']} "
            f"machine_like_folders={report['machine_like_count']}"
        )
        if report["machine_like_names"]:
            print("  machine-like folders:")
            for name in report["machine_like_names"][:10]:
                print(f"    - {name}")
        if report["folder_names"]:
            print("  first folders:")
            for name in report["folder_names"][:10]:
                print(f"    - {name}")
        if report["file_names"]:
            print("  first files:")
            for name in report["file_names"][:10]:
                print(f"    - {name}")


def relative_path_for_item(
    client: GraphClient,
    drive_id: str,
    item: dict[str, Any],
    root_path: str,
    state: SyncState,
) -> str | None:
    current = item
    for _ in range(2):
        rel = _relative_path_from_parent(current, root_path)
        if rel:
            return rel
        item_id = current.get("id")
        if item_id and item_id in state.items:
            return state.items[item_id].get("rel_path")
        if not item_id:
            return None
        current = client.request_json(f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}")
    return None


def _relative_path_from_parent(item: dict[str, Any], root_path: str) -> str | None:
    name = item.get("name")
    parent_path = (item.get("parentReference") or {}).get("path") or ""
    if not name:
        return None
    marker = "root:"
    if marker not in parent_path:
        return name
    folder = parent_path.split(marker, 1)[1].strip("/")
    normalized_root = root_path.strip("/")
    if normalized_root:
        if folder == normalized_root:
            folder = ""
        elif folder.startswith(f"{normalized_root}/"):
            folder = folder[len(normalized_root) + 1:]
    rel = posixpath.normpath(posixpath.join(folder, name))
    if rel == "." or rel.startswith("../") or rel == "..":
        return None
    return rel


def safe_target(root: Path, rel_path: str) -> Path:
    target = (root / rel_path).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise RuntimeError(f"Refusing to write outside PROTO_ROOT: {rel_path}")
    return target


def normalize_sharepoint_path(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/").strip("/"))


def parse_include_paths(value: str | None) -> list[str]:
    if not value:
        return []
    paths = []
    for raw in value.replace("\n", ",").split(","):
        normalized = normalize_sharepoint_path(raw)
        if normalized and normalized != ".":
            paths.append(normalized)
    return sorted(dict.fromkeys(paths))


def path_is_included(rel_path: str, include_paths: list[str]) -> bool:
    if not include_paths:
        return True
    rel = normalize_sharepoint_path(rel_path)
    return any(rel == include or rel.startswith(f"{include}/") for include in include_paths)


def set_mtime(path: Path, timestamp: str | None):
    if not timestamp:
        return
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        ts = parsed.timestamp()
        os.utime(path, (ts, ts))
    except ValueError:
        return


def should_download(item: dict[str, Any], previous: dict[str, Any] | None, target: Path) -> bool:
    if not target.exists():
        return True
    if not previous:
        return True
    return any(
        previous.get(key) != item.get(key)
        for key in ("eTag", "cTag", "size", "lastModifiedDateTime")
    )


def mirror_delta(
    client: GraphClient,
    drive_id: str,
    root_item_id: str | None,
    root_path: str,
    state: SyncState,
    source_root: Path,
    allowed_extensions: set[str],
    full: bool,
    max_downloads: int | None = None,
    include_paths: list[str] | None = None,
) -> dict[str, int]:
    include_paths = include_paths or []
    if full or not state.matches_scope(drive_id, root_item_id, root_path, include_paths):
        state.delta_link = None
        state.items = {}
        state.drive_id = drive_id
        state.root_item_id = root_item_id
        state.root_path = root_path.strip("/")
        state.include_paths = include_paths

    if state.delta_link:
        next_url = state.delta_link
    elif root_item_id:
        next_url = f"/drives/{quote(drive_id, safe='')}/items/{quote(root_item_id, safe='')}/delta"
    else:
        next_url = f"/drives/{quote(drive_id, safe='')}/root/delta"

    counts = {"seen": 0, "downloaded": 0, "deleted": 0, "skipped": 0}
    while next_url:
        payload = client.request_json(next_url)
        for item in payload.get("value", []):
            counts["seen"] += 1
            item_id = item.get("id")
            if not item_id:
                continue
            if item.get("deleted"):
                previous = state.items.pop(item_id, None)
                if previous and previous.get("rel_path"):
                    target = safe_target(source_root, previous["rel_path"])
                    if target.exists():
                        target.unlink()
                        counts["deleted"] += 1
                continue
            if "file" not in item:
                continue
            rel_path = relative_path_for_item(client, drive_id, item, root_path, state)
            if not rel_path:
                counts["skipped"] += 1
                continue
            if not path_is_included(rel_path, include_paths):
                counts["skipped"] += 1
                continue
            ext = Path(rel_path).suffix.lower()
            if allowed_extensions and ext not in allowed_extensions:
                counts["skipped"] += 1
                continue
            target = safe_target(source_root, rel_path)
            previous = state.items.get(item_id)
            if should_download(item, previous, target):
                if max_downloads is not None and counts["downloaded"] >= max_downloads:
                    counts["skipped"] += 1
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = client.request_bytes(
                        f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/content"
                    )
                    tmp = target.with_suffix(target.suffix + ".tmp")
                    tmp.write_bytes(content)
                    tmp.replace(target)
                    set_mtime(target, item.get("lastModifiedDateTime"))
                    counts["downloaded"] += 1
            else:
                counts["skipped"] += 1
            state.items[item_id] = {
                "rel_path": rel_path,
                "eTag": item.get("eTag"),
                "cTag": item.get("cTag"),
                "size": item.get("size"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
            }
        state.delta_link = payload.get("@odata.deltaLink") or state.delta_link
        next_url = payload.get("@odata.nextLink")
    return counts


def rebuild_manifest(source_root: Path, manifest_path: Path, root_mode: str, customer_name: str | None):
    source_root.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(str(source_root), root_mode=root_mode, customer_name=customer_name)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest["summary"]


def run_ingest(args: argparse.Namespace):
    if args.apply_schema:
        from proto.schema import apply_indexes

        apply_indexes()

    command = [sys.executable, "-m", "proto.ingest"]
    if args.ingest_all:
        command.append("--all")
    if args.ingest_force:
        command.append("--force")
    command.extend(["--workers", str(args.ingest_workers)])
    command.extend(["--img-workers", str(args.ingest_img_workers)])
    command.extend(["--machine-workers", str(args.ingest_machine_workers)])
    for value in args.ingest_arg:
        command.append(value)
    subprocess.run(command, check=True)


def parse_extensions(value: str) -> set[str]:
    if value.strip().lower() == "all":
        return set()
    return {ext if ext.startswith(".") else f".{ext}" for ext in value.lower().replace(" ", "").split(",") if ext}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--web-url", default=env("SHAREPOINT_WEB_URL"), help="SharePoint browser URL for the library or folder")
    ap.add_argument("--tenant-id", default=env("SHAREPOINT_TENANT_ID"))
    ap.add_argument("--client-id", default=env("SHAREPOINT_CLIENT_ID"))
    ap.add_argument("--client-secret", default=env("SHAREPOINT_CLIENT_SECRET"))
    ap.add_argument("--site-id", default=env("SHAREPOINT_SITE_ID"))
    ap.add_argument("--site-hostname", default=env("SHAREPOINT_SITE_HOSTNAME"))
    ap.add_argument("--site-path", default=env("SHAREPOINT_SITE_PATH"))
    ap.add_argument("--drive-id", default=env("SHAREPOINT_DRIVE_ID"))
    ap.add_argument("--drive-name", default=env("SHAREPOINT_DRIVE_NAME"))
    ap.add_argument("--root-path", default=env("SHAREPOINT_ROOT_PATH", ""))
    ap.add_argument("--source-root", default=env("PROTO_ROOT", PROTO_ROOT))
    ap.add_argument("--proto-root-mode", choices=["machines", "customers"], default=env("PROTO_ROOT_MODE", "machines"))
    ap.add_argument("--customer-name", default=env("PROTO_CUSTOMER_NAME"), help="Attach this customer name when proto-root-mode=machines")
    ap.add_argument("--manifest-path", default=env("PROTO_MANIFEST_PATH", PROTO_MANIFEST_PATH))
    ap.add_argument(
        "--state-path",
        default=env("SHAREPOINT_DELTA_STATE", str(Path(PROTO_CACHE_DIR) / "sharepoint_delta_state.json")),
    )
    ap.add_argument("--extensions", default=env("SHAREPOINT_EXTENSIONS", ",".join(sorted(DEFAULT_EXTENSIONS))))
    ap.add_argument("--include-paths", default=env("SHAREPOINT_INCLUDE_PATHS"), help="Comma-separated relative paths under the selected root to mirror")
    ap.add_argument("--max-downloads", type=int, default=None, help="Download at most this many files, useful for smoke tests")
    ap.add_argument("--full", action="store_true", help="Ignore saved delta token and rescan the selected library/folder")
    ap.add_argument("--inspect-fit", action="store_true", help="List children and check whether the root matches Proto's machine-folder layout")
    ap.add_argument("--inspect-parent", action="store_true", help="Also inspect the parent of the selected root path")
    ap.add_argument("--no-scan", action="store_true", help="Do not rebuild proto manifest after mirroring")
    ap.add_argument("--apply-schema", action="store_true", help="Apply Proto graph indexes before running ingest")
    ap.add_argument("--run-ingest", action="store_true", help="Run python -m proto.ingest after mirroring")
    ap.add_argument("--ingest-all", action="store_true", help="Pass --all to proto.ingest")
    ap.add_argument("--ingest-force", action="store_true", help="Pass --force to proto.ingest")
    ap.add_argument("--ingest-workers", type=int, default=int(env("PROTO_INGEST_WORKERS", "8")))
    ap.add_argument("--ingest-img-workers", type=int, default=int(env("PROTO_INGEST_IMG_WORKERS", "4")))
    ap.add_argument("--ingest-machine-workers", type=int, default=int(env("PROTO_INGEST_MACHINE_WORKERS", "1")))
    ap.add_argument("--ingest-arg", action="append", default=[], help="Additional raw argument for proto.ingest")
    return ap


def main():
    args = build_parser().parse_args()
    if args.web_url:
        parts = parse_sharepoint_web_url(args.web_url)
        args.site_hostname = args.site_hostname or parts.hostname
        args.site_path = args.site_path or parts.site_path
        args.drive_name = args.drive_name or parts.drive_name
        args.root_path = args.root_path or parts.root_path

    client = GraphClient.from_client_credentials(
        required(args.tenant_id, "SHAREPOINT_TENANT_ID"),
        required(args.client_id, "SHAREPOINT_CLIENT_ID"),
        required(args.client_secret, "SHAREPOINT_CLIENT_SECRET"),
    )
    site_id = resolve_site_id(client, args.site_id, args.site_hostname, args.site_path)
    drive_id = resolve_drive_id(client, site_id, args.drive_id, args.drive_name)

    if args.inspect_fit:
        root_path = (args.root_path or "").strip("/")
        roots = [root_path]
        parent = posixpath.dirname(root_path)
        if args.inspect_parent and parent != root_path:
            roots.append(parent)
        reports = [inspect_root_fit(client, drive_id, root) for root in roots]
        print_fit_report(reports)
        return

    root_item_id = resolve_root_item_id(client, drive_id, args.root_path or "")

    state_path = Path(args.state_path)
    state = SyncState.load(state_path)
    counts = mirror_delta(
        client,
        drive_id,
        root_item_id,
        args.root_path or "",
        state,
        Path(args.source_root),
        parse_extensions(args.extensions),
        args.full,
        max_downloads=args.max_downloads,
        include_paths=parse_include_paths(args.include_paths),
    )
    state.save(state_path)

    print(
        "SharePoint mirror: "
        f"seen={counts['seen']} downloaded={counts['downloaded']} "
        f"deleted={counts['deleted']} skipped={counts['skipped']}"
    )
    if not args.no_scan:
        summary = rebuild_manifest(
            Path(args.source_root),
            Path(args.manifest_path),
            args.proto_root_mode,
            args.customer_name,
        )
        print(
            "Proto manifest: "
            f"machines={summary['machine_count']} pdfs={summary['total_pdfs']} "
            f"images={summary['total_images']} texts={summary['total_texts']}"
        )
    if args.run_ingest:
        run_ingest(args)


if __name__ == "__main__":
    main()
