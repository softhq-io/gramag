"""Probe Gemini RPM tier by ramping concurrent vision calls.

Free tier: 60 RPM (Flash). Tier 1 paid: 1000+ RPM. Tier 2: 2000+ RPM.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from proto.vision import vision_extract_page

PNG_DIR = Path("/Users/piotrzwolinski/projects/gramag/proto/cache/pages")


def collect_pngs(n: int) -> list[str]:
    pngs = []
    for p in PNG_DIR.rglob("*.png"):
        pngs.append(str(p))
        if len(pngs) >= n:
            break
    return pngs


def probe(workers: int, n_calls: int) -> dict:
    pngs = collect_pngs(n_calls)
    print(f"\n=== workers={workers}  calls={len(pngs)} ===")
    t0 = time.time()
    ok = err_429 = err_other = 0
    latencies = []

    def call(p):
        s = time.time()
        try:
            vision_extract_page(p)
            return ("ok", time.time() - s)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "resource_exhausted" in msg or "rate" in msg:
                return ("429", time.time() - s)
            return (f"err: {str(e)[:60]}", time.time() - s)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(call, p) for p in pngs]
        for f in as_completed(futures):
            kind, lat = f.result()
            latencies.append(lat)
            if kind == "ok":
                ok += 1
            elif kind == "429":
                err_429 += 1
            else:
                err_other += 1

    elapsed = time.time() - t0
    rps = len(pngs) / elapsed
    rpm = rps * 60
    return {
        "workers": workers, "calls": len(pngs), "elapsed_s": round(elapsed, 1),
        "ok": ok, "429": err_429, "other_err": err_other,
        "effective_rpm": round(rpm, 1),
        "avg_latency_s": round(sum(latencies) / len(latencies), 2),
    }


def main():
    print("Probing Gemini Flash vision RPM tier…")
    for w in [8, 16, 24, 40]:
        r = probe(w, n_calls=w * 3)
        print(
            f"  -> ok={r['ok']}/{r['calls']} "
            f"429={r['429']} effective_rpm={r['effective_rpm']} "
            f"avg_lat={r['avg_latency_s']}s elapsed={r['elapsed_s']}s"
        )
        if r["429"] > r["calls"] * 0.3:
            print("  >> 30%+ 429 errors — tier ceiling hit at this concurrency.")
            break


if __name__ == "__main__":
    main()
