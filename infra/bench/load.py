"""Bắn tải vào một URL để thử autoscaling (màn 4), thay cho k6 khi máy không cài k6.

    uv run python infra/bench/load.py https://web-1a2b3c.vexenhanh.id.vn --workers 20 --seconds 300

Mỗi 10 giây in số request, số request mỗi giây, số lỗi, và máy nào đã trả lời (app mẫu của
nhóm máy in tên máy), để thấy tải được chia sang máy mới khi nhóm lớn lên.
"""

import argparse
import re
import threading
import time
from collections import Counter

import httpx

HOST = re.compile(r"Máy trả lời: ([\w.-]+)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--workers", type=int, default=20, help="số request chạy song song")
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()

    lock = threading.Lock()
    window: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    latencies: list[float] = []
    stop = time.monotonic() + args.seconds

    def worker() -> None:
        with httpx.Client(timeout=30, headers={"User-Agent": "minicloud-load-test"}) as client:
            while time.monotonic() < stop:
                t0 = time.monotonic()
                try:
                    r = client.get(args.url)
                    status = str(r.status_code)
                    match = HOST.search(r.text)
                except httpx.HTTPError:
                    status, match = "error", None
                with lock:
                    window[status] += 1
                    latencies.append(time.monotonic() - t0)
                    if match:
                        hosts[match.group(1)] += 1

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    started = time.monotonic()
    while any(t.is_alive() for t in threads):
        time.sleep(10)
        with lock:
            total = sum(window.values())
            ok = window.get("200", 0)
            ordered = sorted(latencies)
            p95 = ordered[int(len(ordered) * 0.95)] if ordered else 0.0
            members = ", ".join(f"{h}: {n}" for h, n in sorted(hosts.items()))
            print(
                f"{time.monotonic() - started:5.0f} s | {total / 10:5.1f} req/s | "
                f"lỗi {total - ok} | p95 {p95:.2f} s | {members}",
                flush=True,
            )
            window.clear()
            hosts.clear()
            latencies.clear()


if __name__ == "__main__":
    main()
