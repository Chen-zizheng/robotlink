# -*- coding: utf-8 -*-
"""跑一轮真实链路，把三端日志录到 runs/，再用 align 检查。

    python bench/trace.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from robotlink.align import check, merge       # noqa: E402
from robotlink.cloud import Link               # noqa: E402
from robotlink.inject import Injector          # noqa: E402
from robotlink.record import Recorder          # noqa: E402
from robotlink.robot import Robot, RobotServer  # noqa: E402


def main() -> int:
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")
    os.makedirs(out, exist_ok=True)
    p_cloud = os.path.join(out, "cloud.jsonl")
    p_robot = os.path.join(out, "robot.jsonl")

    rec_c = Recorder(p_cloud)
    rec_r = Recorder(p_robot)
    robot = Robot(recorder=rec_r)
    server = RobotServer(robot).start()
    inj = Injector(target_port=server.port, drop=0.1, seed=23).start()
    link = Link(inj.host, inj.port, timeout=0.8, retries=3, recorder=rec_c).connect()
    try:
        link.request({"cmd": "start", "task": "trace"})
        link.request({"cmd": "query"})
        link.request({"cmd": "stop"})
    finally:
        link.close()
        inj.close()
        server.close()
        rec_c.close()
        rec_r.close()

    res = check(merge([p_cloud, p_robot]))
    print("cloud:", p_cloud)
    print("robot:", p_robot)
    print("rows=%d span=%.3fs violations=%d unanswered=%d" % (
        res["rows"], res["span_s"], res["violation_count"], res["unanswered"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
