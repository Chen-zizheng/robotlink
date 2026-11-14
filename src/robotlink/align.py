# -*- coding: utf-8 -*-
"""把云端、机器人、注入器三份日志按时间戳揉成一条时间线，找两类错误：

1. 时序违例：状态上报早于对应的命令下发（不该发生，发生就是时钟或实现错了）；
2. 丢失：命令发出去了，但既没有 STATUS 也没有 dup 标记。

用法：
    python -m robotlink.align run_cloud.jsonl run_robot.jsonl [run_inject.jsonl]
"""
from __future__ import annotations

import json
import sys

from . import record


def merge(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        rows.extend(record.load(p))
    rows.sort(key=lambda r: r["t"])
    return rows


def check(rows: list[dict], gap_ms: float = 50.0) -> dict:
    t0 = rows[0]["t"] if rows else 0.0
    violations = []
    missed = 0
    pending: dict[int, float] = {}
    for r in rows:
        d = r["data"]
        if r["src"] == "cloud" and r["kind"] == "SEND":
            pending[d["seq"]] = r["t"]
        elif r["src"] == "cloud" and r["kind"] == "RECV":
            sent = pending.pop(d["seq"], None)
            if sent is not None and r["t"] - sent > gap_ms / 1000.0:
                violations.append({
                    "seq": d["seq"],
                    "kind": "slow_ack",
                    "ms": round((r["t"] - sent) * 1000, 1),
                })
            elif sent is None:
                violations.append({"seq": d["seq"], "kind": "recv_without_send"})
    missed = len(pending)
    return {"rows": len(rows), "span_s": round(rows[-1]["t"] - t0, 3) if rows else 0.0,
            "violations": violations[:20], "violation_count": len(violations),
            "unanswered": missed}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    rows = merge(argv[1:])
    res = check(rows)
    for r in rows[:30]:
        print("%10.3f  %-6s %-8s %s" % (r["t"], r["src"], r["kind"], json.dumps(r["data"], ensure_ascii=False)))
    print("-" * 60)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if not res["violations"] and not res["unanswered"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
