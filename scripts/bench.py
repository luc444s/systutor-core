#!/usr/bin/env python3
"""Closed-loop load benchmark for the fastest endpoints.

Usage:
    python scripts/bench.py --base-url http://127.0.0.1:8124 --token <jwt> \
        --levels 10,100,1000,5000,10000,20000 --duration 5

Each worker issues requests back-to-back (closed loop: N concurrent
workers, each reusing keep-alive connections) until the deadline, then
drains its current request. Reports throughput, p50/p95/p99 latency,
error and 503 breakdown per level. Output is JSON to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter

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


async def run_level(client: httpx.AsyncClient, path: str, concurrency: int, duration: float):
    latencies: list[float] = []
    statuses: Counter = Counter()
    errors = 0
    deadline = time.perf_counter() + duration

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
    total = sum(statuses.values()) + errors
    ok = statuses.get(200, 0)
    return {
        "concurrency": concurrency,
        "duration_s": round(duration, 1),
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
        "rejected_503_pct": round(100 * statuses.get(503, 0) / total, 2) if total else 0.0,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8124")
    parser.add_argument("--token", default="")
    parser.add_argument("--levels", default="10,100,1000,5000,10000,20000")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--endpoints", default="health,ready,permissions")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]

    limits = httpx.Limits(max_connections=25000, max_keepalive_connections=25000)
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    results = {}
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, limits=limits, timeout=120.0
    ) as client:
        for name in endpoints:
            path = ENDPOINTS[name]
            results[name] = []
            for level in levels:
                result = await run_level(client, path, level, args.duration)
                result["endpoint"] = name
                results[name].append(result)
                print(json.dumps(result), flush=True)
    print("===RESULTS_JSON_BEGIN===")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
