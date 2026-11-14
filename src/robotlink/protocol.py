# -*- coding: utf-8 -*-
"""帧协议：16 字节头 + 变长负载，CRC16 校验。

    magic(2) ver(1) type(1) seq(2) length(2) crc16(2) payload(length)

seq 由发送端自增，接收端用它判重；crc16 覆盖 type/seq/length/payload。
解析器按字节流喂入，半包等下一批，坏帧丢弃后重新找 magic。
"""
from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

MAGIC = b"\xa5\x5a"
VER = 1
HEADER = struct.Struct(">2sBBHHH")   # magic ver type seq length crc
MAX_PAYLOAD = 4096

CMD, STATUS, ACK, HEARTBEAT, ESTOP = 1, 2, 3, 4, 5
TYPE_NAME = {1: "CMD", 2: "STATUS", 3: "ACK", 4: "HEARTBEAT", 5: "ESTOP"}


@dataclass(frozen=True)
class Frame:
    ftype: int
    seq: int
    payload: bytes

    @property
    def name(self) -> str:
        return TYPE_NAME.get(self.ftype, "UNKNOWN")


def _crc(ftype: int, seq: int, payload: bytes) -> int:
    return binascii.crc_hqx(bytes([ftype]) + struct.pack(">HH", seq, len(payload)) + payload, 0xFFFF)


def encode(ftype: int, seq: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long: %d" % len(payload))
    header = HEADER.pack(MAGIC, VER, ftype, seq & 0xFFFF, len(payload), _crc(ftype, seq, payload))
    return header + payload


class FrameParser:
    """粘包/半包都交给这里，调用方只管 feed。"""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0
        self.resync = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buf += data
        out: list[Frame] = []
        while True:
            if len(self.buf) < HEADER.size:
                return out
            if self.buf[:2] != MAGIC:
                # 找下一个 magic，把中间这段计为一次重同步
                idx = self.buf.find(MAGIC, 1)
                self.resync += 1
                if idx < 0:
                    self.buf.clear()
                    return out
                del self.buf[:idx]
                continue
            _magic, _ver, ftype, seq, length, crc = HEADER.unpack(bytes(self.buf[: HEADER.size]))
            if length > MAX_PAYLOAD:
                self.bad_crc += 1
                del self.buf[:HEADER.size]
                continue
            total = HEADER.size + length
            if len(self.buf) < total:
                return out
            payload = bytes(self.buf[HEADER.size:total])
            if _crc(ftype, seq, payload) != crc:
                self.bad_crc += 1
                del self.buf[:2]          # 丢掉这个 magic，继续往后找
                continue
            out.append(Frame(ftype, seq, payload))
            del self.buf[:total]
