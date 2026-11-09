#!/usr/bin/env python3
"""Multi-process closed-loop benchmark (client bottleneck fix).

Same metrics as scripts/bench.py, but the client load is split across
--procs processes so a single httpx event loop does not saturate one
core while the server idles. Each process runs concurrency/procs
closed-loop workers against the same endpoint/level; results are
aggregated (latency percentiles over the merged sample).

Usage:
    python scripts/bench_mp.py --token <jwt> --procs 4 \
        --levels 10,100,1000,5000,10000,20000 --duration 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import httpx

ENDPOINTS = {
    "health": "/api/v1/system/health/live",
    "ready": "/api/v1/system/health/ready",
    "permissions": "/api/v1/core/permissions",
}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return ordered[idx]


async def _run_chunk(base_url: str, token: str, path: str, concurrency: int, duration: float):
    latencies: list[float] = []
    statuses: Counter = Counter()
    errors = 0
    deadline = time.perf_counter() + duration
    limits = httpx.Limits(max_connections=25000, max_keepalive_connections=25000)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        base_url=base_url, headers=headers, limits=limits, timeout=120.0
    ) as client:
        async def worker() -> None:
            nonlocal errors
            while time.perf_counter() < deadline:
                start = time.perf_counter()
                try:
                    response = await client.get(path)
                    statuses[response.status_code] += 1
                except Exception:
                    errors += 1
                    statuses["error"] += 1
                latencies.append((time.perf_counter() - start) * 1000)

        await asyncio.gather(*[worker() for _ in range(concurrency)])
    return {
        "latencies": latencies,
        "statuses": {str(k): v for k, v in statuses.items()},
        "errors": errors,
    }


def _run_chunk_sync(args):
    base_url, token, path, concurrency, duration = args
    return asyncio.run(_run_chunk(base_url, token, path, concurrency, duration))


def aggregate(endpoint: str, level: int, duration: float, chunks: list[dict]) -> dict:
    latencies: list[float] = []
    statuses: Counter = Counter()
    errors = 0
    for chunk in chunks:
        latencies.extend(chunk["latencies"])
        errors += chunk["errors"]
        for k, v in chunk["statuses"].items():
            statuses[k] += v
    total = sum(statuses.values()) + errors
    ok = statuses.get("200", 0)
    return {
        "endpoint": endpoint,
        "concurrency": level,
        "duration_s": duration,
        "requests": total,
        "ok": ok,
        "errors": errors,
        "statuses": {str(k): v for k, v in statuses.items()},
        "req_per_s_ok": round(ok / duration, 1) if ok else 0.0,
        "req_per_s_all": round(total / duration, 1) if total else 0.0,
        "p50_ms": round(percentile(latencies, 50), 1),
        "p95_ms": round(percentile(latencies, 95), 1),
        "p99_ms": round(percentile(latencies, 99), 1),
        "error_pct": round(100 * errors / total, 2) if total else 0.0,
        "rejected_503_pct": round(100 * statuses.get("503", 0) / total, 2) if total else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8124")
    parser.add_argument("--token", default="")
    parser.add_argument("--levels", default="10,100,1000,5000,10000,20000")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--endpoints", default="health,ready,permissions")
    parser.add_argument("--procs", type=int, default=4)
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    results = {}
    with ProcessPoolExecutor(max_workers=args.procs) as pool:
        for name in endpoints:
            path = ENDPOINTS[name]
            results[name] = []
            for level in levels:
                base, rem = divmod(level, args.procs)
                chunks_args = [
                    (
                        args.base_url,
                        args.token,
                        path,
                        base + (1 if i < rem else 0),
                        args.duration,
                    )
                    for i in range(args.procs)
                ]
                chunks = list(pool.map(_run_chunk_sync, chunks_args))
                result = aggregate(name, level * args.procs if level < args.procs else level, args.duration, chunks)
                # fix reported concurrency to requested total
                result["concurrency"] = level
                results[name].append(result)
                print(json.dumps(result), flush=True)
    print("===RESULTS_JSON_BEGIN===")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
