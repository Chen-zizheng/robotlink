# -*- coding: utf-8 -*-
r"""被测算系统：一台模拟机器人本体 + 一个 TCP 服务端。

状态机（只能沿这些边走，其余一律 409 化）：
    BOOT -> SELF_TEST -> IDLE -> RUNNING -> IDLE
                            \-> ESTOP -> IDLE（需 clear）
    RUNNING -超时-> FAULT -> IDLE（需 clear）
ESTOP 走独立优先级通道：队列里还有任务时也必须当拍停机。
"""
from __future__ import annotations

import json
import socket
import threading
import time

from . import protocol as P

BOOT, SELF_TEST, IDLE, RUNNING, ESTOP, FAULT = "BOOT", "SELF_TEST", "IDLE", "RUNNING", "ESTOP", "FAULT"
ALLOWED = {
    BOOT: {SELF_TEST},
    SELF_TEST: {IDLE, FAULT},
    IDLE: {RUNNING, ESTOP, SELF_TEST},
    RUNNING: {IDLE, ESTOP, FAULT},
    ESTOP: {IDLE},
    FAULT: {IDLE},
}


class Robot:
    def __init__(self, recorder=None, boot_self_test=True):
        self.state = BOOT
        self.task = ""
        self.seen: set[int] = set()       # 去重：seq 幂等
        self.metrics = {"frames_in": 0, "frames_out": 0, "dropped": 0, "dupes": 0, "rejected": 0}
        self._lock = threading.Lock()
        self._rec = recorder
        self._last_beat = 0.0
        if boot_self_test:
            self._goto(SELF_TEST)
            self._goto(IDLE)

    # ---- 状态机 ----
    def _goto(self, nxt: str) -> None:
        self.state = nxt
        self._log("STATE", {"state": nxt})

    def _log(self, kind: str, data: dict) -> None:
        if self._rec:
            self._rec.write("robot", kind, data)

    def command(self, frame: P.Frame) -> tuple[int, dict]:
        """返回 (seq, 响应体)；响应体里带 ok / state / task / reason。"""
        with self._lock:
            self.metrics["frames_in"] += 1
            if frame.seq in self.seen:
                self.metrics["dupes"] += 1
                return frame.seq, {"ok": True, "dup": True, "state": self.state, "task": self.task}
            self.seen.add(frame.seq)
            try:
                body = json.loads(frame.payload.decode("utf-8")) if frame.payload else {}
            except Exception:
                self.metrics["rejected"] += 1
                return frame.seq, {"ok": False, "reason": "bad json", "state": self.state}

            cmd = str(body.get("cmd", ""))
            if cmd == "estop":
                self._goto(ESTOP)
                self._log("ESTOP", {"seq": frame.seq})
                return frame.seq, {"ok": True, "state": self.state, "task": ""}
            if cmd == "query":
                return frame.seq, {"ok": True, "state": self.state, "task": self.task}
            if cmd == "clear":
                if self.state not in (ESTOP, FAULT):
                    self.metrics["rejected"] += 1
                    return frame.seq, {"ok": False, "reason": "nothing to clear", "state": self.state}
                self._goto(IDLE)
                self.task = ""
                return frame.seq, {"ok": True, "state": self.state}
            if cmd == "selftest":
                if self.state not in (IDLE, BOOT):
                    self.metrics["rejected"] += 1
                    return frame.seq, {"ok": False, "reason": "busy", "state": self.state}
                self._goto(SELF_TEST)
                self._goto(IDLE)
                return frame.seq, {"ok": True, "state": self.state}
            if cmd == "start":
                if self.state != IDLE:
                    self.metrics["rejected"] += 1
                    return frame.seq, {"ok": False, "reason": "not idle", "state": self.state}
                self._goto(RUNNING)
                self.task = str(body.get("task", ""))
                return frame.seq, {"ok": True, "state": self.state, "task": self.task}
            if cmd == "stop":
                if self.state != RUNNING:
                    self.metrics["rejected"] += 1
                    return frame.seq, {"ok": False, "reason": "not running", "state": self.state}
                self._goto(IDLE)
                self.task = ""
                return frame.seq, {"ok": True, "state": self.state}
            self.metrics["rejected"] += 1
            return frame.seq, {"ok": False, "reason": "unknown cmd", "state": self.state}


class RobotServer:
    def __init__(self, robot: Robot, host="127.0.0.1", port=0):
        self.robot = robot
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(8)
        self.host, self.port = self.sock.getsockname()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> "RobotServer":
        t = threading.Thread(target=self._accept, daemon=True)
        t.start()
        self._threads.append(t)
        return self

    def _accept(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            t.start()
            self._threads.append(t)

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        parser = P.FrameParser()
        last_beat = 0.0
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    data = b""
                except OSError:
                    return
                if data:
                    for frame in parser.feed(data):
                        if frame.ftype == P.ESTOP:
                            seq, body = self.robot.command(P.Frame(P.CMD, frame.seq, b'{"cmd":"estop"}'))
                        else:
                            seq, body = self.robot.command(frame)
                        out = P.encode(P.STATUS, seq, json.dumps(body, ensure_ascii=False).encode())
                        conn.sendall(out)
                        self.robot.metrics["frames_out"] += 1
                now = time.monotonic()
                if now - last_beat > 0.2:
                    last_beat = now
                    try:
                        conn.sendall(P.encode(P.HEARTBEAT, 0, json.dumps({"state": self.robot.state}).encode()))
                        self.robot.metrics["frames_out"] += 1
                    except OSError:
                        return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
