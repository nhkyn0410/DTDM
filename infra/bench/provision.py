"""Thí nghiệm "Thời gian cấp phát": tạo rồi xoá lần lượt N máy qua API, đo thời gian tới khi chạy.

Mỗi vòng: POST tạo máy → chờ operation `succeeded` → ghi thời gian → xoá máy → chờ xoá xong.
Làm tuần tự nên chỉ cần quota cho thêm một máy. In ra từng vòng và p50, p95 (nearest rank).

    uv run python infra/bench/provision.py --operator secrets/staging-operator.json \
        --type container -n 20 --csv bench-container.csv

File operator là JSON có "api", "api_key", "project_id" (như secrets/staging-operator.json).
"""

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime

import httpx


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(len(ordered) * p / 100)) - 1]


def wait_op(client: httpx.Client, op_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        op = client.get(f"/v1/operations/{op_id}").json()
        if op["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
            return op
        time.sleep(0.25)


def server_seconds(op: dict) -> float | None:
    if not op.get("finished_at"):
        return None
    start = datetime.fromisoformat(op["created_at"])
    end = datetime.fromisoformat(op["finished_at"])
    return (end - start).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator", required=True, help="JSON có api, api_key, project_id")
    parser.add_argument("--type", choices=["container", "virtual-machine"], default="container")
    parser.add_argument("--flavor", default="s1.small")
    parser.add_argument("-n", type=int, default=10, help="số vòng")
    parser.add_argument("--timeout", type=float, default=600, help="giây chờ tối đa mỗi bước")
    parser.add_argument("--csv", help="ghi từng vòng ra file CSV")
    args = parser.parse_args()

    op = json.load(open(args.operator, encoding="utf-8"))
    client = httpx.Client(
        base_url=op["api"], headers={"Authorization": f"Bearer {op['api_key']}"}, timeout=60
    )
    rows = []
    prefix = "bvm" if args.type == "virtual-machine" else "bct"
    for i in range(1, args.n + 1):
        name = f"{prefix}-{int(time.time()) % 100000}-{i}"
        t0 = time.monotonic()
        r = client.post(
            f"/v1/projects/{op['project_id']}/instances",
            json={"name": name, "type": args.type, "flavor": args.flavor},
        )
        if r.status_code != 202:
            sys.exit(f"vòng {i}: tạo máy bị từ chối, HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        created = wait_op(client, body["operation"]["id"], args.timeout)
        client_s = time.monotonic() - t0
        row = {
            "round": i,
            "type": args.type,
            "status": created["status"],
            "client_seconds": round(client_s, 2),
            "server_seconds": server_seconds(created),
            "error": created.get("error") or "",
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        inst_id = body["instance"]["id"]
        deleted = client.delete(f"/v1/instances/{inst_id}")
        if deleted.status_code == 202:
            wait_op(client, deleted.json()["id"], args.timeout)
        if created["status"] != "succeeded":
            break  # a failure needs a look before burning more rounds

    ok = [r["client_seconds"] for r in rows if r["status"] == "succeeded"]
    server = [
        r["server_seconds"] for r in rows if r["status"] == "succeeded" and r["server_seconds"]
    ]
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    if ok:
        summary = {
            "type": args.type,
            "rounds": len(rows),
            "succeeded": len(ok),
            "client_p50": percentile(ok, 50),
            "client_p95": percentile(ok, 95),
            "client_max": max(ok),
            "server_p50": percentile(server, 50) if server else None,
            "server_p95": percentile(server, 95) if server else None,
        }
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
