# -*- coding: utf-8 -*-
import json

import pytest

from robotlink import protocol as P


def test_roundtrip():
    raw = P.encode(P.CMD, 7, b'{"cmd":"start"}')
    frames = P.FrameParser().feed(raw)
    assert len(frames) == 1
    assert frames[0].seq == 7 and frames[0].ftype == P.CMD
    assert json.loads(frames[0].payload) == {"cmd": "start"}


@pytest.mark.parametrize("cut", [1, 2, 5, 11, 16, 17])
def test_half_packet(cut):
    raw = P.encode(P.STATUS, 3, b'{"state":"IDLE"}')
    p = P.FrameParser()
    assert p.feed(raw[:cut]) == []
    frames = p.feed(raw[cut:])
    assert [f.seq for f in frames] == [3]


def test_sticky_packets():
    raw = P.encode(P.CMD, 1, b"a") + P.encode(P.CMD, 2, b"bb") + P.encode(P.CMD, 3, b"")
    frames = P.FrameParser().feed(raw)
    assert [f.seq for f in frames] == [1, 2, 3]


def test_bad_crc_dropped():
    raw = bytearray(P.encode(P.CMD, 9, b'{"cmd":"stop"}'))
    raw[-1] ^= 0xFF                      # 改坏负载
    good = P.encode(P.CMD, 10, b"ok")
    p = P.FrameParser()
    frames = p.feed(bytes(raw) + good)
    assert [f.seq for f in frames] == [10]
    assert p.bad_crc >= 1


def test_garbage_resync():
    p = P.FrameParser()
    frames = p.feed(b"\x00\x11\x22" + P.encode(P.CMD, 4, b"x"))
    assert [f.seq for f in frames] == [4]
    assert p.resync >= 1


def test_oversized_payload_rejected():
    with pytest.raises(ValueError):
        P.encode(P.CMD, 1, b"x" * (P.MAX_PAYLOAD + 1))
