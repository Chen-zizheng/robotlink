# -*- coding: utf-8 -*-
from robotlink import align


def rows(*items):
    out = []
    for t, src, kind, data in items:
        out.append({"t": t, "wall": 0, "src": src, "kind": kind, "data": data})
    return out


def test_clean_timeline_has_no_violation():
    r = rows((1.0, "cloud", "SEND", {"seq": 1, "cmd": "start"}),
             (1.02, "cloud", "RECV", {"seq": 1, "state": "RUNNING"}))
    res = align.check(r)
    assert res["violations"] == [] and res["unanswered"] == 0


def test_slow_ack_flagged():
    r = rows((1.0, "cloud", "SEND", {"seq": 2, "cmd": "start"}),
             (1.40, "cloud", "RECV", {"seq": 2, "state": "RUNNING"}))
    res = align.check(r, gap_ms=50)
    assert res["violation_count"] == 1
    assert res["violations"][0]["kind"] == "slow_ack"
    assert res["violations"][0]["ms"] == 400.0


def test_unanswered_counted():
    r = rows((1.0, "cloud", "SEND", {"seq": 3, "cmd": "stop"}))
    assert align.check(r)["unanswered"] == 1


def test_recv_without_send_flagged():
    r = rows((2.0, "cloud", "RECV", {"seq": 9, "state": "IDLE"}))
    res = align.check(r)
    assert res["violations"][0]["kind"] == "recv_without_send"
