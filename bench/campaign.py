# -*- coding: utf-8 -*-
"""故障矩阵：同一串命令，在五种链路条件下各跑一遍。

    python bench/campaign.py

每个格子记录：重发了多少次、本体收了多少重复包、最后状态是否一致。
这张表就是「断网重连 / 丢包重试」这类用例的实测依据。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from robotlink.cloud import Link          # noqa: E402
from robotlink.inject import Injector     # noqa: E402
from robotlink.robot import IDLE, Robot, RobotServer  # noqa: E402

CASES = [
    ("clean", dict()),
    ("drop10", dict(drop=0.10)),
    ("drop30", dict(drop=0.30)),
    ("dup30", dict(dup=0.30)),
    ("mix", dict(drop=0.20, dup=0.20, delay=0.005)),
]


def run_one(name: str, cfg: dict, ops: int = 60) -> dict:
    robot = Robot()
    server = RobotServer(robot).start()
    inj = Injector(target_port=server.port, seed=17, **cfg).start()
    link = Link(inj.host, inj.port, timeout=0.6, retries=3).connect()
    ok = fail = recovered = 0

    def resync() -> None:
        """超时之后状态未知，先问再收尾，不能让下一轮从错的状态起步。"""
        try:
            state = link.request({"cmd": "query"})["state"]
            if state == "RUNNING":
                link.request({"cmd": "stop"})
            elif state in ("ESTOP", "FAULT"):
                link.request({"cmd": "clear"})
        except (TimeoutError, ConnectionError):
            pass

    try:
        for _ in range(ops):
            try:
                link.request({"cmd": "start", "task": "x"})
                link.request({"cmd": "query"})
                link.request({"cmd": "stop"})
                ok += 1
            except TimeoutError:
                fail += 1
                resync()
                recovered += 1
    finally:
        res = {
            "case": name,
            "rounds_ok": ok,
            "rounds_failed": fail,
            "resynced": recovered,
            "resends": link.dropped,
            "reconnects": link.reconnects,
            "dupes_at_robot": robot.metrics["dupes"],
            "rejected": robot.metrics["rejected"],
            "dropped_chunks": inj.stats["dropped"],
            "duped_chunks": inj.stats["duped"],
            "state": robot.state,
            "consistent": robot.state == IDLE,
        }
        link.close()
        inj.close()
        server.close()
    return res


def main() -> int:
    ops = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rows = [run_one(n, c, ops) for n, c in CASES]
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    bad = [r for r in rows if not r["consistent"]]
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
