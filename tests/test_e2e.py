# -*- coding: utf-8 -*-
"""端到端：真开 socket，中间夹一个会丢包、重复、切片的代理。"""
import time

import pytest

from robotlink import protocol as P
from robotlink.cloud import Link
from robotlink.inject import Injector
from robotlink.robot import ESTOP, IDLE, RUNNING, Robot, RobotServer


@pytest.fixture()
def bench():
    robot = Robot()
    server = RobotServer(robot).start()
    # 按整帧丢/重复：TCP 会把丢掉的字节补回来，但代理是直接吃掉字节，
    # 所以这里切成整帧粒度，等价于真实网络里的包丢失，而不是流被撕开。
    inj = Injector(target_port=server.port, drop=0.3, dup=0.5, seed=11).start()
    yield robot, server, inj
    inj.close()
    server.close()


def test_reaches_running_through_lossy_link(bench):
    robot, server, inj = bench
    link = Link(inj.host, inj.port, timeout=1.0, retries=6).connect()
    body = link.request({"cmd": "start", "task": "deliver"})
    assert body["ok"] is True and robot.state == RUNNING
    body = link.request({"cmd": "stop"})
    assert body["ok"] is True and robot.state == IDLE
    assert inj.stats["in_chunks"] > 0
    for _ in range(20):                       # 多打几轮，让重复包真的落到本体上
        link.request({"cmd": "query"})
    assert robot.metrics["dupes"] >= 1        # 重复包确实发出来了，且没导致重启任务
    assert robot.state == IDLE
    link.close()


def test_estop_wins_over_queued_commands():
    # 不走注入器：这条要测的是「一次写入里排着两条命令时，急停必须插队」，
    # 不是「丢了包还能不能恢复」，混在一起就分不清是谁的功劳。
    robot = Robot()
    server = RobotServer(robot).start()
    link = Link(server.host, server.port, timeout=1.0).connect()
    link.request({"cmd": "start", "task": "walk"})
    assert robot.state == RUNNING
    blob = P.encode(P.CMD, 1001, b'{"cmd":"start"}') + P.encode(P.ESTOP, 1002, b'{"cmd":"estop"}')
    link.sock.sendall(blob)
    for _ in range(40):
        if robot.state == ESTOP:
            break
        try:
            link.sock.recv(4096)
        except OSError:
            break
    assert robot.state == ESTOP
    link.close()
    server.close()


def test_reconnect_after_injected_disconnect():
    robot = Robot()
    server = RobotServer(robot).start()
    inj = Injector(target_port=server.port, disconnect_after=0.05, seed=3).start()
    link = Link(inj.host, inj.port, timeout=0.4, retries=2).connect()
    ok = 0
    time.sleep(0.1)                       # 让代理先到点断一次
    for i in range(5):
        try:
            link.request({"cmd": "query"})
            ok += 1
        except TimeoutError:
            pass
    assert ok >= 1
    assert link.reconnects >= 1
    link.close()
    inj.close()
    server.close()
