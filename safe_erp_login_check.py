"""Safe login check for Gramag ERP API.

This script performs:
- POST /get-webToken

Optionally, when --read-graphql is enabled, it performs one extra read-only
GraphQL introspection query to discover available query fields.
Optionally, when --full-graphql-exploration is enabled, it performs a full
read-only schema introspection and saves results to a local JSON file.

It does NOT perform any write/mutation operation.
It never prints token values.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


DEFAULT_BASE_URL = "https://api.exxas.net"
LOGIN_PATH = "/get-webToken"
SENSITIVE_KEYS = {"apiKey", "bearerToken", "apiKeyExpiresIn24Hours"}
URL_KEYS_WITH_SENSITIVE_QUERY = {"graphQlV2Url", "swaggerV2Url"}
READ_ONLY_INTROSPECTION_QUERY = """
query ReadOnlySchemaProbe {
  __schema {
    queryType {
      name
      fields {
        name
      }
    }
    mutationType {
      name
    }
  }
}
"""
FULL_SCHEMA_INTROSPECTION_QUERY = """
query FullSchemaExploration {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind
      name
      description
      fields(includeDeprecated: true) {
        name
        description
        isDeprecated
        deprecationReason
        args {
          name
          description
          defaultValue
          type { ...TypeRef }
        }
        type { ...TypeRef }
      }
      inputFields {
        name
        description
        defaultValue
        type { ...TypeRef }
      }
      interfaces { ...TypeRef }
      enumValues(includeDeprecated: true) {
        name
        description
        isDeprecated
        deprecationReason
      }
      possibleTypes { ...TypeRef }
    }
    directives {
      name
      description
      locations
      args {
        name
        description
        defaultValue
        type { ...TypeRef }
      }
    }
  }
}

fragment TypeRef on __Type {
  kind
  name
  ofType {
    kind
    name
    ofType {
      kind
      name
      ofType {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
            ofType {
              kind
              name
            }
          }
        }
      }
    }
  }
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe login test for Gramag ERP API.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("EXXAS_BASE_URL", DEFAULT_BASE_URL),
        help=f"ERP API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("EXXAS_USER"),
        help="ERP username/email (or set EXXAS_USER).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("EXXAS_PASSWORD"),
        help="ERP password (or set EXXAS_PASSWORD). If missing, prompt securely.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Request timeout in seconds (default: 15).",
    )
    parser.add_argument(
        "--read-graphql",
        action="store_true",
        help="Run one read-only GraphQL introspection query after successful login.",
    )
    parser.add_argument(
        "--graphql-url",
        default=os.environ.get("EXXAS_GRAPHQL_URL"),
        help=(
            "GraphQL URL to probe (or set EXXAS_GRAPHQL_URL). "
            "If omitted, uses graphQlV2Url from login response."
        ),
    )
    parser.add_argument(
        "--graphql-max-fields",
        type=int,
        default=40,
        help="Maximum number of query field names to print (default: 40).",
    )
    parser.add_argument(
        "--full-graphql-exploration",
        action="store_true",
        help="Run full read-only GraphQL schema introspection and save to JSON file.",
    )
    parser.add_argument(
        "--exploration-output",
        default="graphql_full_exploration.json",
        help="Output JSON path for full exploration (default: graphql_full_exploration.json).",
    )
    return parser.parse_args()


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    user = (args.user or "").strip()
    password = args.password

    if not user:
        user = input("ERP user/email: ").strip()

    if not password:
        password = getpass.getpass("ERP password: ")

    if not user or not password:
        raise ValueError("Missing username or password.")

    return user, password


def redact_secret(value: str) -> str:
    if not value:
        return "<redacted>"
    return f"<redacted:{len(value)} chars>"


def redact_url_query_value(url: str, key_to_redact: str = "issuedFor") -> str:
    try:
        parsed = urlparse(url)
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key == key_to_redact:
                query.append((key, "<redacted>"))
            else:
                query.append((key, value))
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return "<redacted-url>"


def build_redacted_payload(payload: dict) -> dict:
    redacted = {}
    for key, value in payload.items():
        if key in SENSITIVE_KEYS and isinstance(value, str):
            redacted[key] = redact_secret(value)
        elif key in URL_KEYS_WITH_SENSITIVE_QUERY and isinstance(value, str):
            redacted[key] = redact_url_query_value(value)
        else:
            redacted[key] = value
    return redacted


def graphql_read_probe(
    graphql_url: str,
    api_key: str,
    timeout: float,
    max_fields: int,
) -> tuple[bool, dict]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {"query": READ_ONLY_INTROSPECTION_QUERY}

    try:
        response = requests.post(
            graphql_url,
            json=body,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return False, {"reason": f"network/request error: {exc}"}

    if response.status_code != 200:
        preview = response.text.strip().replace("\n", " ")
        return False, {
            "reason": "non-200 response",
            "status_code": response.status_code,
            "response_preview": preview[:300] if preview else "",
        }

    try:
        payload = response.json()
    except ValueError:
        return False, {"reason": "response is not valid JSON"}

    if payload.get("errors"):
        return False, {"reason": "graphql returned errors", "errors": payload["errors"][:3]}

    schema = payload.get("data", {}).get("__schema", {})
    query_type = schema.get("queryType") or {}
    mutation_type = schema.get("mutationType") or {}
    query_fields = query_type.get("fields") or []
    field_names = [f.get("name") for f in query_fields if f.get("name")]

    return True, {
        "queryType": query_type.get("name"),
        "mutationType": mutation_type.get("name"),
        "queryFieldsCount": len(field_names),
        "queryFieldsPreview": field_names[: max(0, max_fields)],
    }


def graphql_full_exploration(
    graphql_url: str,
    api_key: str,
    timeout: float,
) -> tuple[bool, dict]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {"query": FULL_SCHEMA_INTROSPECTION_QUERY}

    try:
        response = requests.post(
            graphql_url,
            json=body,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return False, {"reason": f"network/request error: {exc}"}

    if response.status_code != 200:
        preview = response.text.strip().replace("\n", " ")
        return False, {
            "reason": "non-200 response",
            "status_code": response.status_code,
            "response_preview": preview[:300] if preview else "",
        }

    try:
        payload = response.json()
    except ValueError:
        return False, {"reason": "response is not valid JSON"}

    if payload.get("errors"):
        return False, {"reason": "graphql returned errors", "errors": payload["errors"][:3]}

    return True, payload


def build_full_exploration_summary(schema_payload: dict, max_fields: int) -> dict:
    schema = schema_payload.get("data", {}).get("__schema", {})
    types = schema.get("types") or []
    directives = schema.get("directives") or []

    kind_counts = {}
    for item in types:
        kind = item.get("kind", "UNKNOWN")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    query_type_name = (schema.get("queryType") or {}).get("name")
    mutation_type_name = (schema.get("mutationType") or {}).get("name")
    subscription_type_name = (schema.get("subscriptionType") or {}).get("name")

    query_fields = []
    if query_type_name:
        query_type = next((t for t in types if t.get("name") == query_type_name), None)
        query_fields = (query_type or {}).get("fields") or []

    query_field_names = [f.get("name") for f in query_fields if f.get("name")]
    object_names = sorted(
        t.get("name")
        for t in types
        if t.get("kind") == "OBJECT"
        and t.get("name")
        and not str(t.get("name")).startswith("__")
    )

    return {
        "queryType": query_type_name,
        "mutationType": mutation_type_name,
        "subscriptionType": subscription_type_name,
        "totalTypes": len(types),
        "typeCountsByKind": kind_counts,
        "totalDirectives": len(directives),
        "directivesPreview": [d.get("name") for d in directives[: max(0, max_fields)] if d.get("name")],
        "queryFieldsCount": len(query_field_names),
        "queryFieldsPreview": query_field_names[: max(0, max_fields)],
        "objectTypesCount": len(object_names),
        "objectTypesPreview": object_names[: max(0, max_fields)],
    }


def main() -> int:
    args = parse_args()

    try:
        user, password = resolve_credentials(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    login_url = args.base_url.rstrip("/") + LOGIN_PATH

    try:
        response = requests.post(
            login_url,
            json={"user": user, "password": password},
            timeout=args.timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        print("LOGIN_FAILED")
        print(f"reason: network/request error: {exc}")
        return 1

    if response.status_code != 200:
        print("LOGIN_FAILED")
        print(f"status_code: {response.status_code}")
        body_preview = response.text.strip().replace("\n", " ")
        if body_preview:
            print(f"response_preview: {body_preview[:300]}")
        return 1

    try:
        payload = response.json()
    except ValueError:
        print("LOGIN_FAILED")
        print("reason: response is not valid JSON")
        return 1

    # Never print secrets; show only presence flags + redacted response shape.
    has_api_key = bool(payload.get("apiKey"))
    has_bearer = bool(payload.get("bearerToken"))
    redacted_payload = build_redacted_payload(payload)

    print("LOGIN_OK")
    print(f"user: {user}")
    print(f"status_code: {response.status_code}")
    print(f"api_key_received: {has_api_key}")
    print(f"bearer_token_received: {has_bearer}")
    print("redacted_response:")
    print(json.dumps(redacted_payload, indent=2, ensure_ascii=True))

    if args.read_graphql:
        if not has_api_key:
            print("GRAPHQL_READ_SKIPPED")
            print("reason: apiKey not present in login response")
            return 1

        graphql_url = (args.graphql_url or payload.get("graphQlV2Url") or "").strip()
        if not graphql_url:
            print("GRAPHQL_READ_SKIPPED")
            print("reason: graphql URL not provided and missing from login response")
            return 1

        ok, info = graphql_read_probe(
            graphql_url=graphql_url,
            api_key=payload["apiKey"],
            timeout=args.timeout,
            max_fields=args.graphql_max_fields,
        )

        if not ok:
            print("GRAPHQL_READ_FAILED")
            print(f"graphql_url: {redact_url_query_value(graphql_url)}")
            print(json.dumps(info, indent=2, ensure_ascii=True))
            return 1

        print("GRAPHQL_READ_OK")
        print(f"graphql_url: {redact_url_query_value(graphql_url)}")
        print(json.dumps(info, indent=2, ensure_ascii=True))

    if args.full_graphql_exploration:
        if not has_api_key:
            print("GRAPHQL_FULL_EXPLORATION_SKIPPED")
            print("reason: apiKey not present in login response")
            return 1

        graphql_url = (args.graphql_url or payload.get("graphQlV2Url") or "").strip()
        if not graphql_url:
            print("GRAPHQL_FULL_EXPLORATION_SKIPPED")
            print("reason: graphql URL not provided and missing from login response")
            return 1

        ok, full_payload = graphql_full_exploration(
            graphql_url=graphql_url,
            api_key=payload["apiKey"],
            timeout=args.timeout,
        )

        if not ok:
            print("GRAPHQL_FULL_EXPLORATION_FAILED")
            print(f"graphql_url: {redact_url_query_value(graphql_url)}")
            print(json.dumps(full_payload, indent=2, ensure_ascii=True))
            return 1

        summary = build_full_exploration_summary(full_payload, args.graphql_max_fields)

        try:
            with open(args.exploration_output, "w", encoding="utf-8") as f:
                json.dump(full_payload, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            print("GRAPHQL_FULL_EXPLORATION_FAILED")
            print(f"reason: cannot write output file: {exc}")
            return 1

        print("GRAPHQL_FULL_EXPLORATION_OK")
        print(f"graphql_url: {redact_url_query_value(graphql_url)}")
        print(f"output_file: {args.exploration_output}")
        print("exploration_summary:")
        print(json.dumps(summary, indent=2, ensure_ascii=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
