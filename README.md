# robotlink

机器人上下位机的联调工具：一个带状态机的本体模拟器、一个会重发的云端客户端、一个能在链路中间丢包/重复/乱序/切片的代理，外加把三端日志按时间戳对齐的检查脚本。

用来回答一类具体问题：任务下发下去没反应，是云端没发、链路丢了、本体没收、还是收了但状态机拒绝了？

## 目录

| 模块 | 做的事 |
| --- | --- |
| [protocol.py](src/robotlink/protocol.py) | 10 字节头 + 变长负载，CRC16 校验；流式解析器处理粘包、半包、坏帧重同步 |
| [robot.py](src/robotlink/robot.py) | 被测系统：BOOT/SELF_TEST/IDLE/RUNNING/ESTOP/FAULT 状态机，急停抢占，seq 去重 |
| [cloud.py](src/robotlink/cloud.py) | 客户端：seq 自增、超时同 seq 重发、断线重连、心跳单独计时 |
| [inject.py](src/robotlink/inject.py) | TCP 字节流代理：按 recv 块或 MTU 切片丢弃、重复、乱序、延迟、到点断连 |
| [record.py](src/robotlink/record.py) | 带单调时钟的 JSONL 录制 |
| [align.py](src/robotlink/align.py) | 多端日志合并成时间线，按云端 SEND/RECV 找慢应答、无应答和无对应发送的接收 |
| [campaign.py](bench/campaign.py) | 五种链路条件各跑 20 轮，输出完成轮次、超时、重发、重复和最终状态 |
| [soak.py](bench/soak.py) | 长稳：边跑任务边丢包，按秒采样 RSS、线程数、句柄数 |

## 快速开始

```bash
pip install pytest          # 只跑测试的话只需要这个
python -m pytest -q         # 24 passed

python bench/campaign.py 20 # 故障矩阵，输出 JSON 行
python bench/soak.py --seconds 30 --drop 0.2 --dup 0.2
python bench/trace.py       # 录一轮三端日志，再对齐检查
```

[pyproject.toml](pyproject.toml) 为 pytest 配置了 `pythonpath = ["src"]`，所以从仓库根目录运行测试无需安装本项目；bench 脚本自行设置源码路径。运行代码需要 Python 3.10+。长稳脚本可选安装 `psutil` 采样 RSS / 线程 / 句柄；未安装时部分指标不可用。

## 帧格式

```
magic(2)=A5 5A  ver(1)=1  type(1)  seq(2)  length(2)  crc16(2)  payload(length)
```

头部由 `struct.Struct(">2sBBHHH")` 编码，共 10 字节，多字节字段为大端。type 取 CMD / STATUS / ACK / HEARTBEAT / ESTOP。crc16 覆盖 type、seq、length 和 payload，不含 magic 和 ver。校验用 `binascii.crc_hqx(..., 0xFFFF)`，改一字节就重算。

负载上限 4096 字节，`encode()` 里就拦住，避免解析端拿到一个 length 字段说 60KB 的帧还得先分配内存。

## 五个实现决定

**1. 坏帧丢 2 字节，不丢整帧。** 收到 CRC 错的帧时，无法确定是负载坏了还是长度字段坏了，只好丢掉 magic 这两个字节，从下一个字节重新找 `A5 5A`。代价是可能多扫几遍，好处是不会因为一个坏 length 把后面的好帧一起吞掉。

**2. 重发用同一个 seq，不新起一条。** 超时说明「不知道对方收没收到」，这时换新 seq 会让本体把同一条任务当两条执行。本体侧用 `seen: set[int]` 记已处理的 seq，重复包直接回 `dup: true`，不改状态。

**3. 急停不走队列。** 一次 write 里排着 start 和 estop 两帧时，estop 必须在当拍生效。`Robot.command()` 里 estop 分支放在所有状态检查之前，队列里还有什么都不看。测试 `test_estop_wins_over_queued_commands` 就是专门造这个场景。

**4. 注入器操作字节块，不解析协议帧。** 代理按 `recv(4096)` 得到的块处理，配置 `mtu` 时再切片；一个块可能包含半帧或多帧，因此 drop / dup 不保证对应整帧。它测试的是应用层字节流受损后的重同步与超时恢复，不是 TCP 底层丢包重传模型。campaign 使用同一代理，不能把其丢弃比例直接解释为协议帧丢失率。

**5. 日志按单调时钟对齐，不按序号。** 丢包之后 seq 是不连续的，按序号拼三端日志必然错位。录制时同时写 `t`（`time.monotonic()`）和 `wall`，对齐只用 `t`。

## 测试与历史运行记录

下列故障矩阵、30 秒长稳和 trace 数字保留自原 README，仓库未保存对应原始输出；原记录未注明完整系统、Python 版本和硬件，本次未复跑这些实验。当前脚本默认随机种子 17，campaign 使用 `timeout=0.6`、`retries=3`；即使固定种子，TCP 分块和线程调度仍可能改变结果。

### 故障矩阵（每种链路 20 轮，每轮 start → query → stop）

| 链路条件 | 成功轮次 | 超时 | 重发 | 本体收到重复包 | 结束时状态 |
| --- | --- | --- | --- | --- | --- |
| 无故障 | 20/20 | 0 | 0 | 0 | IDLE |
| 丢 10% | 20/20 | 0 | 12 | 5 | IDLE |
| 丢 30% | 16/20 | 4 | 62 | 17 | IDLE |
| 重复 30% | 20/20 | 0 | 0 | 17 | IDLE |
| 丢 20% + 重复 20% + 延迟 5ms | 19/20 | 1 | 34 | 36 | IDLE |

原记录合计 100 轮，其中 95 轮未抛出 `TimeoutError`（轮内允许重发，不是“一次发送通过”）。脚本在超时后尝试 query，再按状态 stop / clear；`resynced` 统计恢复尝试，不保证恢复成功。表中最终 IDLE 也不等于每轮状态一致性已被断言。重复 seq 不重复执行由状态机测试覆盖。

### 长稳（30 秒，丢 20% + 重复 20%）

```json
{"seconds": 30.3, "ok_ops": 54, "timeouts": 1, "ops_per_s": 1.78,
 "dupes_seen_by_robot": 24, "rejected": 2,
 "injector": {"in_chunks": 320, "dropped": 68, "duped": 56},
 "rss_start_mb": 23.06, "rss_end_mb": 23.17, "rss_mb_per_min": 0.22,
 "threads_end": 7, "handles_end": 172, "final_state": "IDLE"}
```

原记录中 30 秒完成 54 次操作、1 次超时，RSS 增长 0.11 MB，末次线程数 7、句柄数 172。窗口过短，不能据此判断是否泄漏，也不能区分堆分配、缓存或连接资源的影响。

### 单元测试

2026-11-15 本次在 Windows 11、Python 3.13.13、pytest 9.1.1 执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -p no:cacheprovider -q tests
# 24 passed in 14.48s
```

共 24 个测试项（含参数化展开），分四组：

- 协议 11 项（6 个测试函数，其中半包测试展开 6 个参数）：往返、6 种切分位置各喂一次半包、一包三帧的粘包、坏 CRC 后仍能取出后面的好帧、前缀脏数据重同步、超长负载在编码端被拒。
- 状态机 6 条：启动/停止、非法迁移被拒且计数、急停后必须 clear、重复 seq 幂等、迁移表里每个目标状态都存在。
- 端到端 3 条：丢包+重复的链路上走完 start/stop、一次写入里 estop 抢占、代理到点断连后客户端重连。
- 日志对齐 4 条：干净时间线无违例、慢应答按毫秒标出、只发未收算无应答、只收未发也算违例。

[bench/trace.py](bench/trace.py) 跑一轮本机 TCP 模拟链路（丢弃比例 10%），生成云端和本体 JSONL 日志，再调用对齐检查。原 README 记录 2026-11-14 一次输出为 11 行、0.842 秒、0 违例、0 无应答；未保留原始日志，本次未验证。

## 已知限制

- 长稳只跑了 30 秒。内存斜率 0.22 MB/min 在 30 分钟尺度上是否成立，没测。
- 注入器只做 TCP 层故障，不模拟 CAN 帧错误、UDP 丢序；CAN 那部分在 [canfault](https://github.com/Chen-zizheng/canfault)。
- 状态机的 RUNNING 没有超时迁移到 FAULT 的定时器，只有显式命令才会迁移。
- 没有做多客户端并发压测，`RobotServer` 每个连接一个线程；连接数增长时的资源上限和瓶颈尚未测量。
- [align.py](src/robotlink/align.py) 只匹配云端 SEND/RECV，报告慢应答、无应答、无对应发送的接收；没有做跨端时钟偏移估计。单调时钟只适用于当前同机进程场景，跨机器不能直接相减。

## License

MIT
