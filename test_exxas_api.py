"""Test script for Gramag ERP (exxas.net) API — auth, swagger discovery, GraphQL probe."""
import os, sys, json
import requests

BASE_URL = "https://api.exxas.net"
USER = os.environ.get("EXXAS_USER", "")
PASSWORD = os.environ.get("EXXAS_PASSWORD", "")


def login() -> dict:
    """Returns full login response: apiKey, bearerToken, graphQlV2Url, swaggerV2Url, systemInfo."""
    r = requests.post(
        f"{BASE_URL}/get-webToken",
        json={"user": USER, "password": PASSWORD},
        timeout=15,
    )
    print(f"POST /get-webToken → {r.status_code}")
    r.raise_for_status()
    return r.json()


def open_swagger_ui(swagger_url: str) -> None:
    print(f"\n─── Swagger UI ───")
    print(f"  Open in browser: {swagger_url}")
    print(f"  (The spec JSON is not exposed separately; Swagger UI loads it internally.)")


def gql_call(graphql_url: str, api_key: str, query: str, variables: dict | None = None, auth_style: str = "bearer") -> dict:
    headers = {"Content-Type": "application/json"}
    if auth_style == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_style == "apikey":
        headers["X-API-KEY"] = api_key
    body = {"query": query}
    if variables is not None:
        body["variables"] = variables
    r = requests.post(graphql_url, json=body, headers=headers, timeout=30)
    return {"status": r.status_code, "body": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text}


def graphql_introspect(graphql_url: str, api_key: str) -> None:
    print(f"\n─── GraphQL introspection ───")
    query = """
    { __schema { queryType { name fields { name description type { name kind ofType { name kind } } } }
                 mutationType { name }
                 types { name kind description } } }
    """
    # Try both auth styles
    for style in ("bearer", "apikey", "none"):
        headers = {"Content-Type": "application/json"}
        if style == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif style == "apikey":
            headers["X-API-KEY"] = api_key
        r = requests.post(graphql_url, json={"query": query}, headers=headers, timeout=30)
        print(f"  [{style}] → {r.status_code}")
        if r.status_code == 200 and "errors" not in (r.json() or {}):
            data = r.json()
            break
        else:
            try:
                print(f"    {json.dumps(r.json())[:300]}")
            except Exception:
                print(f"    {r.text[:300]}")
    else:
        print("  All auth styles failed")
        return

    schema = data.get("data", {}).get("__schema", {})
    q = schema.get("queryType") or {}
    m = schema.get("mutationType") or {}
    types = schema.get("types", [])
    domain_types = [t for t in types if not t["name"].startswith("__") and t["kind"] == "OBJECT"]
    print(f"  queryType={q.get('name')}  mutationType={m.get('name')}  types={len(types)}  domainObjects={len(domain_types)}")

    fields = q.get("fields", []) or []
    print(f"\n  Root query fields ({len(fields)}):")
    for f in fields[:40]:
        t = f.get("type", {})
        tname = t.get("name") or t.get("ofType", {}).get("name") or t.get("kind")
        desc = (f.get("description") or "")[:60]
        print(f"    {f['name']:<40} : {tname:<25} {desc}")
    if len(fields) > 40:
        print(f"    ... and {len(fields)-40} more")

    print(f"\n  Sample domain objects:")
    for t in domain_types[:40]:
        print(f"    {t['name']}")
    if len(domain_types) > 40:
        print(f"    ... and {len(domain_types)-40} more")


def graphql_query(graphql_url: str, api_key: str, query: str) -> None:
    r = requests.post(
        graphql_url,
        json={"query": query},
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=30,
    )
    print(f"\nGraphQL query → {r.status_code}")
    print(r.text[:800])


if __name__ == "__main__":
    if not USER or not PASSWORD:
        raise SystemExit("Set EXXAS_USER and EXXAS_PASSWORD before running this probe.")

    print(f"=== Exxas API test (user={USER}) ===\n")
    info = login()
    api_key = info["apiKey"]
    bearer = info["bearerToken"]
    gql_url = info["graphQlV2Url"]
    sw_url = info["swaggerV2Url"]
    sys_info = info.get("systemInfo", {})
    print(f"Mandant: {sys_info.get('mandantInfo', {}).get('bezeichnung')}  (sysId={sys_info.get('sysId')})")
    print(f"apiKey[:40]: {api_key[:40]}...")
    print(f"bearer[:20]: {bearer[:20]}...")
    print(f"GraphQL: {gql_url}")
    print(f"Swagger: {sw_url}")

    open_swagger_ui(sw_url)
    graphql_introspect(gql_url, api_key)

    # Optional ad-hoc query from CLI: python test_exxas_api.py '{ customers(limit:1) { id } }'
    if len(sys.argv) > 1:
        graphql_query(gql_url, api_key, sys.argv[1])
