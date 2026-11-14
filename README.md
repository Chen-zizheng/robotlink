# robotlink

机器人上下位机的联调工具：一个带状态机的本体模拟器、一个会重发的云端客户端、一个能在链路中间丢包/重复/乱序/切片的代理，外加把三端日志按时间戳对齐的检查脚本。

用来回答一类具体问题：任务下发下去没反应，是云端没发、链路丢了、本体没收、还是收了但状态机拒绝了？

## 目录

| 模块 | 做的事 |
| --- | --- |
| `src/robotlink/protocol.py` | 16 字节头 + 变长负载，CRC16 校验；流式解析器处理粘包、半包、坏帧重同步 |
| `src/robotlink/robot.py` | 被测算系统：BOOT/SELF_TEST/IDLE/RUNNING/ESTOP/FAULT 状态机，急停抢占，seq 去重 |
| `src/robotlink/cloud.py` | 客户端：seq 自增、超时同 seq 重发、断线重连、心跳单独计时 |
| `src/robotlink/inject.py` | TCP 代理：丢包、重复、乱序、延迟、按 MTU 切片、到点断连 |
| `src/robotlink/record.py` | 带单调时钟的 JSONL 录制 |
| `src/robotlink/align.py` | 多端日志合并成时间线，找慢应答、无应答、上报早于下发 |
| `bench/campaign.py` | 五种链路条件各跑 20 轮，输出重发/重复/状态一致性 |
| `bench/soak.py` | 长稳：边跑任务边丢包，按秒采样 RSS、线程数、句柄数 |

## 快速开始

```bash
pip install pytest          # 只跑测试的话只需要这个
python -m pytest -q         # 20 passed

python bench/campaign.py 20 # 故障矩阵，输出 JSON 行
python bench/soak.py --seconds 30 --drop 0.2 --dup 0.2
```

`pyproject.toml` 里配了 `pythonpath = ["src"]`，不用装包也能 import。

## 帧格式

```
magic(2)=A5 5A  ver(1)=1  type(1)  seq(2)  length(2)  crc16(2)  payload(length)
```

type 取 CMD / STATUS / ACK / HEARTBEAT / ESTOP。crc16 覆盖 type、seq、length 和 payload，不含 magic。校验用 `binascii.crc_hqx(..., 0xFFFF)`，改一字节就重算。

负载上限 4096 字节，`encode()` 里就拦住，避免解析端拿到一个 length 字段说 60KB 的帧还得先分配内存。

## 五个实现决定

**1. 坏帧丢 2 字节，不丢整帧。** 收到 CRC 错的帧时，无法确定是负载坏了还是长度字段坏了，只好丢掉 magic 这两个字节，从下一个字节重新找 `A5 5A`。代价是可能多扫几遍，好处是不会因为一个坏 length 把后面的好帧一起吞掉。

**2. 重发用同一个 seq，不新起一条。** 超时说明「不知道对方收没收到」，这时换新 seq 会让本体把同一条任务当两条执行。本体侧用 `seen: set[int]` 记已处理的 seq，重复包直接回 `dup: true`，不改状态。

**3. 急停不走队列。** 一次 write 里排着 start 和 estop 两帧时，estop 必须在当拍生效。`Robot.command()` 里 estop 分支放在所有状态检查之前，队列里还有什么都不看。测试 `test_estop_wins_over_queued_commands` 就是专门造这个场景。

**4. 注入器按整帧丢包，不按字节。** 真实网络里 TCP 会重传丢掉的字节，流不会断；代理在中间吃掉一段字节，等于把后面的帧撕开，测出来的是「协议能不能重同步」而不是「丢了包能不能恢复」。两种都要测，但不能混在一个用例里，所以 campaign 用整帧丢，protocol 的测试单独覆盖切片撕裂。

**5. 日志按单调时钟对齐，不按序号。** 丢包之后 seq 是不连续的，按序号拼三端日志必然错位。录制时同时写 `t`（`time.monotonic()`）和 `wall`，对齐只用 `t`。

## 实测

### 故障矩阵（每种链路 20 轮，每轮 start → query → stop）

| 链路条件 | 成功轮次 | 超时 | 重发 | 本体收到重复包 | 结束时状态 |
| --- | --- | --- | --- | --- | --- |
| 无故障 | 20/20 | 0 | 0 | 0 | IDLE |
| 丢 10% | 20/20 | 0 | 12 | 5 | IDLE |
| 丢 30% | 16/20 | 4 | 62 | 17 | IDLE |
| 重复 30% | 20/20 | 0 | 0 | 17 | IDLE |
| 丢 20% + 重复 20% + 延迟 5ms | 19/20 | 1 | 34 | 36 | IDLE |

五种条件共 100 轮，95 轮一次通过；5 次超时之后先 query 再按状态收尾（RUNNING 就 stop，ESTOP/FAULT 就 clear），下一轮都能从 IDLE 起跑。重复包最多的一格是 36 个，本体没有因为重复包二次启动任务。

### 长稳（30 秒，丢 20% + 重复 20%）

```json
{"seconds": 30.3, "ok_ops": 54, "timeouts": 1, "ops_per_s": 1.78,
 "dupes_seen_by_robot": 24, "rejected": 2,
 "injector": {"in_chunks": 320, "dropped": 68, "duped": 56},
 "rss_start_mb": 23.06, "rss_end_mb": 23.17, "rss_mb_per_min": 0.22,
 "threads_end": 7, "handles_end": 172, "final_state": "IDLE"}
```

30 秒里 54 次成功操作、1 次超时，RSS 涨了 0.11 MB，线程数停在 7，句柄 172。0.22 MB/min 这个斜率更像是 Python 堆没回收，不是连接泄漏——真要下结论得跑够 30 分钟，见「已知限制」。

### 单元测试

20 条，分三组：

- 协议 6 条：往返、6 种切分位置各喂一次半包、一包三帧的粘包、坏 CRC 后仍能取出后面的好帧、前缀脏数据重同步、超长负载在编码端被拒。
- 状态机 6 条：启动/停止、非法迁移被拒且计数、急停后必须 clear、重复 seq 幂等、迁移表里每个目标状态都存在。
- 端到端 3 条：丢包+重复的链路上走完 start/stop、一次写入里 estop 抢占、代理到点断连后客户端重连。

## 已知限制

- 长稳只跑了 30 秒。内存斜率 0.22 MB/min 在 30 分钟尺度上是否成立，没测。
- 注入器只做 TCP 层故障，不模拟 CAN 帧错误、UDP 丢序；CAN 那部分在 [canfault](https://github.com/Chen-zizheng/canfault)。
- 状态机的 RUNNING 没有超时迁移到 FAULT 的定时器，只有显式命令才会迁移。
- 没有做多客户端并发压测，`RobotServer` 每个连接一个线程，连接数上百时会先卡在 accept 上。
- `align.py` 只标了「慢应答」和「无应答」，没有做跨端时钟偏移估计。

## License

MIT
