# -*- coding: utf-8 -*-
import json

import pytest

from robotlink import protocol as P
from robotlink.robot import ALLOWED, ESTOP, IDLE, RUNNING, Robot


def cmd(robot, seq, payload):
    return robot.command(P.Frame(P.CMD, seq, json.dumps(payload).encode()))


def test_boot_to_idle():
    r = Robot()
    assert r.state == IDLE


def test_start_stop():
    r = Robot()
    seq, body = cmd(r, 1, {"cmd": "start", "task": "patrol"})
    assert body["ok"] and r.state == RUNNING and r.task == "patrol"
    seq, body = cmd(r, 2, {"cmd": "stop"})
    assert body["ok"] and r.state == IDLE


def test_illegal_transition_rejected():
    r = Robot()
    seq, body = cmd(r, 1, {"cmd": "stop"})
    assert body["ok"] is False and body["reason"] == "not running"
    assert r.metrics["rejected"] == 1
    cmd(r, 2, {"cmd": "start"})
    seq, body = cmd(r, 3, {"cmd": "start"})
    assert body["ok"] is False and body["reason"] == "not idle"


def test_estop_then_clear():
    r = Robot()
    cmd(r, 1, {"cmd": "start"})
    seq, body = cmd(r, 2, {"cmd": "estop"})
    assert r.state == ESTOP
    seq, body = cmd(r, 3, {"cmd": "start"})
    assert body["ok"] is False
    seq, body = cmd(r, 4, {"cmd": "clear"})
    assert body["ok"] and r.state == IDLE


def test_duplicate_seq_is_idempotent():
    r = Robot()
    seq, first = cmd(r, 10, {"cmd": "start", "task": "a"})
    seq, second = cmd(r, 10, {"cmd": "start", "task": "a"})
    assert first.get("dup") is None
    assert second["dup"] is True
    assert r.metrics["dupes"] == 1
    assert r.task == "a"


def test_every_state_has_transitions():
    assert set(ALLOWED) == {"BOOT", "SELF_TEST", "IDLE", "RUNNING", "ESTOP", "FAULT"}
    for src, dsts in ALLOWED.items():
        for d in dsts:
            assert d in ALLOWED, "%s -> %s 目标不存在" % (src, d)
