# -*- coding: utf-8 -*-
"""录制：一行一个 JSON，带单调时钟。

跨端对齐只能靠时间戳，不能用「第几条」——丢包之后序号是对不上的。
"""
from __future__ import annotations

import json
import threading
import time


class Recorder:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.fh = open(path, "w", encoding="utf-8", newline="\n")

    def write(self, src: str, kind: str, data: dict) -> None:
        rec = {"t": round(time.monotonic(), 6), "wall": round(time.time(), 3),
               "src": src, "kind": kind, "data": data}
        with self._lock:
            self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.fh.flush()

    def close(self) -> None:
        with self._lock:
            self.fh.close()


def load(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
