# v1.2 测试 — 缺陷与偏差清单

> 配套：`REPORT.md`（执行报告）、`EVIDENCE.md`（命令↔产物索引）
> 日期：2026-09-14　被测：`E:\Work\ZT\card_simulator_host`（无 commit）
> 分级依据：`docs/test-plan-v1.2.md` §8.2

**总览：P0 = 0，P1 = 0，P2 = 2，P3 = 1；方案（文档）缺陷/偏差 = 7。**
本文件只**记录与建议**，**未实施任何修改**。

---

## 一、被测仓库内缺陷

### F1 — P2 — stock POS mock 永远读不到 host 的 ack（`on_message` 内等待 ack 阻塞同一 reader 循环）

**现象（直接观测）**
用仓库自带 `tools/mock_clients/mock_pos.py` 跑任一案例，POS 端日志在「上报结果」之后必然出现：

```
[pos] ack timeout (15.0s) for transaction_result
[pos] WARNING: no ack for transaction_result (not persisted?)
```

而**同一轮**的 host 侧证据表明结果**已被持久化并已 ack**：

* `results/VISA-CL-001.json` 存在且 `status=PASS`、`verdict` 四条规则齐备；
* host 日志 `transaction result received for VISA-CL-001 attempt <id> -> pass`；
* `run.json` 正常收尾（`FINISHED`）。

**根因（代码定位）**

| 位置 | 内容 |
|---|---|
| `tools/mock_clients/mock_common.py:152` | `MockClient.run()` 的读循环**顺序**执行 `await self.on_message(self, frame)` |
| `tools/mock_clients/mock_pos.py:142` | `on_message()` 末尾 `ack = await client.send_and_wait_ack(...)` |
| `tools/mock_clients/mock_common.py:106-128` | `send_and_wait_ack()` 注册 future 后 `await asyncio.wait_for(fut, timeout)` |

即：唯一能读到 `MSG_ACK` 的代码是 `run()` 的读循环，而它此刻正**阻塞在 `on_message` 内部**等待该 ack → 自锁，直到 15 s 超时。`mock_android.py` 不使用 `send_and_wait_ack`（一律 `send_ack` 即发即忘），因此**只有 POS mock 受影响**。

**影响**

1. 方案 **§4.3 观测点 6**（`ack for transaction_result: ok=True detail=persisted`）**无法产生**；
2. 方案 **§5.2** 期望的 POS 侧「第二次 ack `detail=duplicate`」**无法观测**（host 侧 `duplicate transaction_result ignored` 仍可观测，本轮已用自建客户端补齐）；
3. D2「先落盘后 ack」在 **POS 端侧无法验证**；更糟的是该 warning **把「host 已落盘」与「host 根本没 ack」渲染成同一种表现**，会掩盖真实回归；
4. 同一根因的连带效应：任何耗时较长的 `on_message` 都会阻塞读循环，使上游帧（如 `session_abort`、`stop_emulation`）无法及时消费。已实测：
   * `--delay 9999`（方案 §4.9 场景）期间 POS mock 收不到 `session_abort`，host 打 `session_abort to pos not acked: ... ack timeout for <msg_id>`；
   * `--interactive` 的阻塞式 `input()` 同理。

**最小复现**

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host serve --trace-enabled                     # 终端 A
python tools\mock_clients\mock_android.py --tap-delay 0.5    # 终端 B
python tools\mock_clients\mock_pos.py --delay 1              # 终端 C
python -m emv_host run --scheme visa --mode contactless --case VISA-CL-001
# 观察：终端 C 在 case_started 约 1s 后打印 ack timeout (15.0s) + WARNING
# 对照：runs\<日期>\<run_id>\results\VISA-CL-001.json 已是 PASS
```

自动化复现：`scripts/l3_scenarios.py M_pos_ack_deadlock`（6/6 断言通过，即缺陷稳定复现）。
证据：`evidence/L3_M_pos_ack_deadlock.json`、`evidence/proc/M_pos_ack_deadlock_pos.log`。

**判定说明**：**host 自身行为正确**（先落盘、后 ack、去重、隔离均按预期），缺陷在测试工具链（`tools/mock_clients/`）。因它使方案两处验收观测点不可达，按 §8.2 归为**可观测性缺失 P2**；若把「方案记载的关键日志行必须出现」视为验收硬条件，则可上调为 P1，**该定级请统筹/用户裁决**。

**修复方向（仅建议，未实施）**
`MockClient` 把消息处理派发到独立 task（`asyncio.create_task(self.on_message(...))`）从而保持读循环空闲；或把「等 ack」改到读循环之外（例如用 `asyncio.Queue` 收发分离）。后者更贴近真实客户端的并发模型。

---

### F2 — P2 — `run.abort_reason` 格式随失败路径不一致（断言失败路径缺案例号）

**现象（直接观测，同一字段两种形状）**

| 失败路径 | 实测 `run.json.abort_reason` | 是否含案例号 |
|---|---|---|
| 断言不符（VISA-CL-001 期望 approved、mock 给 declined） | `case_failed:assertion_failed` | **否** |
| 卡端未连接 | `case_failed:VISA-CL-001:android_not_connected` | 是 |
| POS 未连接 | `case_failed:VISA-CL-001:pos_not_connected` | 是 |
| 结果超时 | `case_failed:VISA-CL-001:result_timeout` | 是 |
| 内核参数被拒 | `case_failed:VISA-CL-001:pos_kernel_config_apply_failed` | 是 |

方案 §4.6 记载的形式是 `case_failed:VISA-CL-001:assertion_failed`（**含**案例号），与实测不符。

**根因（代码定位）**

* `executor.py:181-183`：`_fail()` 会设置 `session.fatal_reason = f"case_failed:{case_id}:{reason}"`（**含**案例号）；
* `executor.py:553-558`：若 `fatal_reason` 已置，则 `run["abort_reason"] = session.fatal_reason`；否则走 `stopped_by_failure` 分支写 `f"case_failed:{stop_reason}"`（**不含**案例号）；
* 断言失败源自 `_judge` 的判定结果（`executor.py:857-859` 置 `failure_reason="assertion_failed"`），**从不经过 `_fail()`**，因此落入后者。

**影响**：同一字段两种语义，消费方（报表/前端/§7.3 的 `run_cases.py`）需分别处理；案例号虽可从 `results/<case>.json` 或其余案例的 `note=run_stopped_after_failure:<case_id>` 恢复，**非数据丢失**，故定 P2。

**最小复现**：`scripts/l3_scenarios.py D_assertion_failed_stops_run` → `evidence/L3_D_assertion_failed_stops_run.json`（D 与 E1/F/G 对比即可）。

**修复方向（仅建议）**：让断言失败也经 `_fail()`；或在 `stopped_by_failure` 分支同样拼上 `session.current_case_id`（并在 §3.2 对应红线里同步期望值，注意该线目前只覆盖 `result_timeout` 路径，不会因此变红）。

---

### F3 — P3 — `results/<case>.json` schema 随写入路径分裂（`attempt` vs `attempts`）

**现象（直接观测）**

| 结果来源 | 实际字段 |
|---|---|
| 真实交易结果（PASS/WARN/FAIL），`executor.py:970` | `"attempt": <0 基整数>`，**无** `attempts` |
| 未收到结果的收尾（ABORTED/SKIPPED/ERROR/inconclusive），`executor.py:1011-1024` | `"attempts": <1 基计数>`，**无** `attempt` |

例：VISA-CL-001 PASS 记录含 `"attempt": 0`；同一轮里手动 abort 的记录为 `{"status":"ABORTED","attempts":1,"note":"run aborted"}`。

方案 §4.3 的落盘核对命令读的正是 `attempts`：

```powershell
Get-Content "$run\results\VISA-CL-001.json" | ConvertFrom-Json | Select-Object case_id,status,attempts
```

对 PASS 案例该列**恒为空**（本轮实测 `attempts` 为 null），容易误读为「字段缺失」。

**根因**：`executor.py:985-989` 与 `executor.py:1011-1024` 两条写入路径各写各的字段；`run["results"][case_id]` 内部索引则恒用 `attempts`（`executor.py:987/1008`），三者不一致。

**影响**：纯可观测性/契约问题，不影响判定正确性。

**最小复现**：对比 `evidence/L3_A_single_pass.json`（PASS）与 `evidence/L3_J_manual_abort.json`（ABORTED）的 `extra.result`。

**修复方向（仅建议）**：统一为 `attempt`（0 基）与 `attempts`（计数）二者并存，或统一只留一个并同步方案与前端读取处。

---

## 二、方案（文档）缺陷与偏差

> 以下均为 `docs/test-plan-v1.2.md` 自身的问题，**不是被测代码缺陷**，但会直接影响后续轮次的验收判定，建议随本轮结论一并修订。

### P-D1 — §6.5 失败验证的示例数据不是坏数据，且该失败路径在 stock mock 上不可达

* 原文：「把 `term` 改成奇数长度的 hex（如 `9F660436000080`）→ 案例应 FAIL + `failure_reason == pos_kernel_config_apply_failed`」。
* 实测（直接观测）：`9F660436000080` 是 **14 个 hex 字符 = 7 字节（偶数）**，且 `decode_tlv_map` 解得合法 BER-TLV `{'9F66': '36000080'}` → host 校验**无任何 error**，案例 **PASS**。
  → 证据 `evidence/L3_TD0_fail_plan_example_term.json`、`evidence/TD0_validate_kp.log`。
* 补充：真·奇数长度（15 字符 `9F6604360000809`）会被 host 加载期拦下记为 **error issue**，但案例**仍 PASS**——因为 stock mock 没有内核、对任何参数都回 `ok=true`，故 `pos_kernel_config_apply_failed` **在 stock mock 上不可达**。
* 要真正走到 `executor.py:760-763`，需要一个回 `ok=false` 的 POS 端。本轮已用 reject 变体补齐：案例 `FAIL`、`failure_reason=pos_kernel_config_apply_failed`、run `ABORTED` → 证据 `evidence/L3_TD0_fail_reject_kernel.json`。
* **建议**：把示例换成真正不可解析的值（奇数长度或非法 TLV），并把「apply 失败」明确标注为「需 POS 端支持才可端到端验证」。

### P-D2 — §6.2 与 §6.3 对 CAPK 数量自相矛盾

* §6.2 明文要求**不要**把 `RID=A000000333 / 9F22=08`（`DF05=PHWWIPQI`）抄进 JSON；§3.4 U-4 亦断言该值在模型层被拒。
* §6.3 又要求「AID/CAPK 数量与资产一致」。
* 二者互斥：资产 29 条，按 §6.2 生成后为 **28 条**（其余 AID 19 条、TERM 1 条与资产一致）。
* **建议**：§6.3 改为「AID 19 / CAPK 28（资产 29 减去 1 条 PHWWIPQI 异常样例）/ TERM 1」并注明排除原因。

### P-D3 — §4.8 期望卡端收到 `stop_emulation`；实际收到 `session_abort`

* 实测（直接观测）：`pos_not_connected` 在**内核参数下发步骤**即抛出（`executor.py:755-759`），此时尚未进入 `_wait_result()`；而向卡端发 `stop_emulation` 的 `_send_stop(CLIENT_ANDROID, ...)` 只在 `_wait_result()` 的 `finally` 中调用（`executor.py:873-880`）。
* 故卡端**收不到** `stop_emulation`，但**收到** `session_abort reason=pos_not_connected case=VISA-CL-001`（v1.2 的异常收尾通道），即「已被告知停止」。
* 证据：`evidence/L3_F_pos_not_connected.json`（卡端日志无 `status: stopped`，有 `session_abort reason=pos_not_connected`）。
* **建议**：§4.8 的观测点改为 `session_abort`（或明确区分「`WAIT_RESULT` 之前失败 → 仅 `session_abort`」与「之后失败 → `stop_emulation` + `session_abort`」）。
* **需对端确认的连带项**（不属 host 缺陷）：真实卡端必须把 `session_abort` 视同「停止模拟」，否则早期失败路径下 HCE 会话可能滞留。建议写入 P3 的对端通知文档。

### P-D4 — §5.2「quarantine.jsonl 不存在」只对「同一 attempt 内」的重传成立

* 实测（直接观测）：`Session.begin_attempt()` 会在**每个案例尝试开始时**重置 `judged_msg_ids`（`session.py:61`），因此去重窗口 = 当前 attempt。
  * 同一 attempt 内重传（本轮 `L4_2`）：ack `detail=duplicate`，`quarantine.jsonl` **不存在** → 与方案一致。
  * 重传在**下一个案例已开始**后到达（本轮 `L4_2b`）：`attributed=False` → 走 `_quarantine()`，ack `detail=quarantined`、`quarantine.jsonl` **被写出**（reason=`case_id mismatch`）。
* 证据：`evidence/L3_L4_2_duplicate_result.json`、`evidence/L3_L4_2b_late_resend_is_quarantined.json`。
* **判定**：实现自洽（晚到结果本就该进隔离区），方案表述过于绝对。**建议** §5.2 限定为「同一 attempt 窗口内的重传」，并补一条「跨案例晚到重传 → `quarantined` + 写入 `quarantine.jsonl`」作为预期行为。

### P-D5 — §4.3 落盘核对命令读取不存在的字段

见 F3：PASS 记录无 `attempts`。**建议**方案改为 `attempt`，或与 F3 的修复方向保持一致。

### P-D6 — §0.3 的两类环境限制本轮均未出现

* 实测：`WinError 5` 0 次、`PermissionError` 0 次，未使用 `--basetemp`；`CTRL_BREAK_EVENT` 可用（host 以 CTRL_BREAK 优雅退出，退出码 0、日志 `bye`、锁被解除）。
* 原因（推断）：本会话文件策略为 `danger-full-access` 且运行于有控制台的终端。
* **建议**：把 §0.3 明确写成「**受限沙箱/无控制台时**才会出现」的条件性免责，以免后续执行者误判。

### P-D7 — §4.9 的 `--interactive` 依赖控制台 stdin

* 方案的「POS mock 不按回车」在子进程化/无 stdin 环境下不可靠（`input()` 可能立刻 EOF → mock 异常退出）。
* 本轮改用等价做法：stock mock `--delay 9999`，观测点与预期完全一致（案例在 15 s 预算后 `result_timeout`）。
* **建议**：方案改述为「`--delay` 设为远大于案例 `timeout_seconds` 的值」。

---

## 三、环境/工具观察（非缺陷，不必修）

1. **对端被强杀时的日志噪声**：peer 被强杀时 host 打
   `ERROR emv_host.protocol | unexpected connection error for ('127.0.0.1', <port>): [WinError 64] ...` 并附完整 traceback（`server.py:84-85` 的 `logger.exception`）；
   正常 `close` 则为 INFO `connection closed: ... peer closed connection`（`server.py:82-83`）。
   本轮所有 traceback 均发生在用例**判定完成之后**的收尾阶段，不影响任何判定。建议后续降噪（例如把 `ConnectionResetError` 归入常规断连分支）。
2. **pytest 配置 warning**：`PytestConfigWarning: No files were found in testpaths`，但 `testpaths=["tests"]` 下仍正常收集 130 项、退出码 0。纯提示。
3. **控制台代码页乱码**：部分 `*.log`（由 `>` 重定向捕获）中的中文为 GBK/UTF-8 混读所致；落盘 JSON 均显式 UTF-8，未见乱码。

---

## 四、待裁决事项（请你决定，我不擅自处理）

1. **F1 的定级**：P2（可观测性缺失）还是 P1（方案记载的关键日志行不可达）？
2. **F1 是否要在本轮修**：它属 `tools/mock_clients/`（测试工具链），不在 §0.1 列出的 host P0+P1 范围内；修与不修都会影响后续 L5 的证据可比性。
3. **F2/F3 是否本轮修**：两者都涉及「字段语义」，一旦改字段会影响前端与 `tools/joint_debug` 的读取，建议与方案修订一起做。
4. **P-D1…P-D7 的修订**：是否由我把这些修订落到 `docs/test-plan-v1.2.md`（或另出一份勘误），还是仅保留在本清单由统筹处理。
5. **`cases/visa/kernel_params.json` 的去留**：该文件含 CAPK 模数（密钥材料），已确认 gitignore 生效、未外发。L5 需要它；是否需要在你确认前保留，或以 `.bak` 暂存。
