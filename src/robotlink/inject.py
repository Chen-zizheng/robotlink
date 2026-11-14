# -*- coding: utf-8 -*-
"""故障注入代理：夹在云端和本体之间，按规则改字节流。

支持丢包、重复、乱序、延迟、按 MTU 切片（制造半包）、到点断连。
只在 TCP 层做手脚，两端代码一行都不用改——这正是它能当测试工具的原因。
"""
from __future__ import annotations

import random
import socket
import threading
import time


class Injector:
    def __init__(self, target_host="127.0.0.1", target_port=0, host="127.0.0.1", port=0,
                 drop=0.0, dup=0.0, delay=0.0, reorder=False, mtu=0, disconnect_after=0.0,
                 seed=7):
        self.target = (target_host, target_port)
        self.drop, self.dup, self.delay = drop, dup, delay
        self.reorder, self.mtu = reorder, mtu
        self.disconnect_after = disconnect_after
        self.rnd = random.Random(seed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(8)
        self.host, self.port = self.sock.getsockname()
        self.stats = {"in_chunks": 0, "dropped": 0, "duped": 0, "reordered": 0, "bytes": 0}
        self._stop = threading.Event()

    def start(self) -> "Injector":
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def _accept(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                u = socket.create_connection(self.target, timeout=2.0)
            except OSError:
                c.close()
                continue
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pump, args=(a, b), daemon=True).start()

    def _chunks(self, data: bytes) -> list[bytes]:
        if not self.mtu:
            return [data]
        return [data[i:i + self.mtu] for i in range(0, len(data), self.mtu)]

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        t0 = time.monotonic()
        held: list[bytes] = []
        try:
            while not self._stop.is_set():
                if self.disconnect_after and time.monotonic() - t0 > self.disconnect_after:
                    break
                try:
                    data = src.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not data:
                    break
                self._forward(dst, data, held)
            if held:
                dst.sendall(held.pop())
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def _forward(self, dst: socket.socket, data: bytes, held: list[bytes]) -> None:
        for chunk in self._chunks(data):
            self.stats["in_chunks"] += 1
            self.stats["bytes"] += len(chunk)
            if self.drop and self.rnd.random() < self.drop:
                self.stats["dropped"] += 1
                continue
            if self.reorder:
                if not held:
                    held.append(chunk)        # 先扣一片，下一片到了再交换
                    continue
                self.stats["reordered"] += 1
                dst.sendall(chunk)
                dst.sendall(held.pop())
            else:
                dst.sendall(chunk)
            if self.dup and self.rnd.random() < self.dup:
                self.stats["duped"] += 1
                dst.sendall(chunk)
            if self.delay:
                time.sleep(self.delay)

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
