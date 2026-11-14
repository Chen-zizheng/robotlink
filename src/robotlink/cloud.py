# -*- coding: utf-8 -*-
"""客户端：代表云端下发任务、收状态上报。

三件事必须做对，否则「断网重连」这种用例根本测不出来：
1. seq 自增且重连后不重置，本体靠 seq 去重，不会把同一条任务执行两次；
2. 超时重发同一帧（同 seq），不是发一帧新的；
3. 心跳只喂「最近一次收到时间」，不参与请求匹配。
"""
from __future__ import annotations

import json
import socket
import time

from . import protocol as P


class Link:
    def __init__(self, host: str, port: int, timeout: float = 0.8, retries: int = 2, recorder=None):
        self.host, self.port = host, port
        self.timeout = timeout
        self.retries = retries
        self.seq = 0
        self.sock: socket.socket | None = None
        self.parser = P.FrameParser()
        self.last_seen = 0.0
        self.reconnects = 0
        self.sent = 0
        self.dropped = 0
        self._rec = recorder

    # ---- 连接 ----
    def connect(self, attempts: int = 5) -> "Link":
        for i in range(attempts):
            try:
                s = socket.create_connection((self.host, self.port), timeout=self.timeout)
                s.settimeout(self.timeout)
                self.sock = s
                self.parser = P.FrameParser()
                self.last_seen = time.monotonic()
                return self
            except OSError:
                time.sleep(0.05 * (i + 1))
        raise ConnectionError("cannot reach %s:%d" % (self.host, self.port))

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _ensure(self) -> None:
        if self.sock is None:
            self.connect()
            self.reconnects += 1

    # ---- 收发 ----
    def request(self, payload: dict, retries: int | None = None) -> dict:
        """发一条命令，等同 seq 的 STATUS；超时用同一个 seq 重发。"""
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        frame = P.encode(P.CMD, seq, json.dumps(payload, ensure_ascii=False).encode())
        for _ in range((self.retries if retries is None else retries) + 1):
            self._ensure()
            try:
                self.sock.sendall(frame)
                self.sent += 1
                if self._rec:
                    self._rec.write("cloud", "SEND", {"seq": seq, "cmd": payload.get("cmd", "")})
                body = self._wait(seq)
                if body is not None:
                    return body
            except OSError:
                self.close()
            self.dropped += 1
        raise TimeoutError("no status for seq=%d" % seq)

    def _wait(self, seq: int) -> dict | None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                self.close()
                return None
            if not data:
                self.close()
                return None
            for frame in self.parser.feed(data):
                self.last_seen = time.monotonic()
                if frame.ftype == P.HEARTBEAT:
                    continue
                if frame.ftype == P.STATUS and frame.seq == seq:
                    body = json.loads(frame.payload.decode("utf-8"))
                    if self._rec:
                        self._rec.write("cloud", "RECV", {"seq": seq, "state": body.get("state")})
                    return body
        return None

    def ping(self) -> bool:
        """半开连接检测：发一帧 query，收不到就当断线。"""
        try:
            self.request({"cmd": "query"}, retries=0)
            return True
        except (TimeoutError, ConnectionError):
            return False

    def estop(self) -> dict:
        """急停不等重试：发一帧 ESTOP，超时也返回，让上层决定。"""
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        self._ensure()
        self.sock.sendall(P.encode(P.ESTOP, seq, b'{"cmd":"estop"}'))
        if self._rec:
            self._rec.write("cloud", "ESTOP", {"seq": seq})
        return {"sent": True, "seq": seq}
