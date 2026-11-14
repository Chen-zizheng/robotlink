# -*- coding: utf-8 -*-
"""robotlink —— 机器人上下位机的联调与故障注入工具。

    protocol  帧格式与流式解析（半包/粘包/坏 CRC 重同步）
    robot     被测算系统：状态机 + 急停抢占 + seq 幂等
    cloud     客户端：超时同 seq 重发、断线重连
    inject    TCP 代理：丢包/重复/乱序/延迟/切片/断连
    record    带单调时钟的 JSONL 录制
    align     多端日志按时间戳对齐，找时序违例
"""
from .protocol import Frame, FrameParser, encode, CMD, STATUS, ACK, HEARTBEAT, ESTOP
from .robot import Robot, RobotServer
from .cloud import Link
from .inject import Injector
from .record import Recorder

__all__ = [
    "Frame", "FrameParser", "encode", "CMD", "STATUS", "ACK", "HEARTBEAT", "ESTOP",
    "Robot", "RobotServer", "Link", "Injector", "Recorder",
]
