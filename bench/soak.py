# -*- coding: utf-8 -*-
"""长稳：一边跑任务，一边往链路里丢包/重复，按秒采样内存与线程。

    python bench/soak.py --seconds 30 --drop 0.2 --dup 0.2

输出最后一行是 JSON，方便塞进 README 或对比两次改动。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from robotlink.cloud import Link          # noqa: E402
from robotlink.inject import Injector     # noqa: E402
from robotlink.robot import Robot, RobotServer  # noqa: E402

try:
    import psutil
except ImportError:                        # 没有 psutil 就只测吞吐
    psutil = None


def rss_mb() -> float:
    if psutil is None:
        return -1.0
    return round(psutil.Process().memory_info().rss / 1048576, 2)


def handles() -> int:
    if psutil is None or not hasattr(psutil.Process(), "num_handles"):
        return -1
    return psutil.Process().num_handles()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--drop", type=float, default=0.2)
    ap.add_argument("--dup", type=float, default=0.2)
    ap.add_argument("--timeout", type=float, default=0.8)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    robot = Robot()
    server = RobotServer(robot).start()
    inj = Injector(target_port=server.port, drop=args.drop, dup=args.dup, seed=5).start()
    link = Link(inj.host, inj.port, timeout=args.timeout, retries=args.retries).connect()

    ok = fail = 0
    samples = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            samples.append((time.monotonic(), rss_mb(), threading.active_count(), handles()))
            time.sleep(1.0)

    threading.Thread(target=sampler, daemon=True).start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        try:
            link.request({"cmd": "start", "task": "soak"})
            link.request({"cmd": "stop"})
            ok += 2
        except TimeoutError:
            fail += 1
            try:
                link.estop()
                link.request({"cmd": "clear"})
            except TimeoutError:
                pass
    stop.set()
    span = time.monotonic() - t0
    states = [s for s in samples if s[1] >= 0]
    drift = (states[-1][1] - states[0][1]) / (span / 60) if len(states) > 1 else 0.0
    summary = {
        "seconds": round(span, 1),
        "ok_ops": ok,
        "timeouts": fail,
        "ops_per_s": round(ok / span, 2),
        "reconnects": link.reconnects,
        "dupes_seen_by_robot": robot.metrics["dupes"],
        "rejected": robot.metrics["rejected"],
        "injector": inj.stats,
        "rss_start_mb": states[0][1] if states else -1,
        "rss_end_mb": states[-1][1] if states else -1,
        "rss_mb_per_min": round(drift, 2),
        "threads_end": states[-1][2] if states else -1,
        "handles_end": states[-1][3] if states else -1,
        "final_state": robot.state,
    }
    print(json.dumps(summary, ensure_ascii=False))
    link.close()
    inj.close()
    server.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
