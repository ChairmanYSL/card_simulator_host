# EMV Test Host v1.2 — 测试方案（P0+P1 部分）

> 交付对象：执行测试的 agent / 工程师。
> 被测范围：本仓库 `E:\Work\ZT\card_simulator_host` 已完成的 v1.2 **P0+P1** 改动（T1–T6、T8–T13，以及 T19 的 mock 部分）。
> 编写日期：2026-09-11。文档只描述测试，不含实现建议。

---

## 0. 执行者必读

### 0.1 本轮交付的边界

已实现、需要测试的：

* 协议层升到 1.2（硬切换）：握手、坏帧、ack 超时、`session_abort`；
* POS 内核参数下发通路（`pos_kernel_config`：AID/CAPK/TERM）及其校验；
* run loop 的 FAIL 语义：任一阶段 ack 超时/拒绝/断线 → 该案例 FAIL → **整轮立即停止**，剩余案例落 SKIPPED；
* `transaction_result` 的「先落盘后 ack」（D2）与重传去重；
* 原子落盘、加载器容错、写路径防逃逸、Web `kind=group`。

**未实现、不要测**：P2 全部（`tools/export_kernel_params.py`、`tools/gen_cases.py` 的 kernel_params 生成、前端 T14–T18、`tools/joint_debug` 的 T20）；P3 全部（`docs/protocol.md`/README/CHANGELOG/对端通知文档）。测试中若发现文档仍写 1.1，**属预期**，不要报缺陷。

### 0.2 仓库没有提交历史

`git rev-list --count HEAD` 会失败：该仓库**一个 commit 都没有**（`git status` 全部是 `??`）。因此：

* **没有 git 基线可回退**，也没有 `git diff` 可看；
* 执行前请**整目录复制一份快照**（例如 `E:\Work\ZT\_snapshot\card_simulator_host_<日期>`），任何「改坏了要还原」的情况靠快照；
* 不要在测试过程中 `git commit`（会把执行者的身份/时间混进历史），除非统筹明确要求。

### 0.3 环境的两个已知限制（**仅限受限沙箱/无控制台，不是被测代码的缺陷**）

> P-D6 修订：这两条**只在受限环境**出现。2026-09-14 的独立执行轮（文件策略为
> `danger-full-access`、有控制台的终端）实测两者**都没有发生**：`WinError 5` 0 次、
> 未用 `--basetemp`、`CTRL_BREAK_EVENT` 可用（host 优雅退出、退出码 0、`bye`、锁已解除）。
> 因此不要把下列现象当缺陷上报，也不要在正常环境里预防性地套用规避手段。

1. **受限沙箱会挡住 pytest 的临时目录**：此时在 `backend` 下直接 `python -m pytest` 会得到
   大量 `PermissionError: [WinError 5] ... 'pytest-of-<user>'`，
   发生在 pytest `tmp_path` 的创建/清理（`os.scandir` / `os.rmdir`）阶段，**测试逻辑本身没跑**。
   → 若无权限放宽策略，请在**普通 shell** 里跑；若仍被挡，用「可写且无需清理」的 basetemp：
   `python -m pytest --basetemp=E:\Work\ZT\card_simulator_host\tmp\pt_<随机数>`
   注意：pytest 结束时仍会尝试 delete 该目录，沙箱下会留下空目录——**空目录残留是环境现象，不算失败**。
2. **Windows 信号测试**依赖控制台：`test_launch.py::test_serve_from_backend_dir_uses_repo_defaults` 与
   `test_shutdown.py` 里的子进程用例需要 `CTRL_BREAK_EVENT` 可用（有控制台的普通终端）。无控制台时会失败，属环境现象。

### 0.4 并发纪律（重要）

根据 2026-09-09 轮次制约定：**同一时间只允许一轮 L5**，host 是统筹方。

* 执行前先确认没有其它 agent 正在跑 L5；有则等；
* L5 期间**两端不装/不重启应用**；
* 任一端要改代码 → 先 `[PAUSE-REQ]`，得到 `[PAUSE-ACK]` 后再动；
* L1–L4 是本仓库内的离线测试，可与其他 agent 并发，但**不要并发跑 `--basetemp` 同一个目录**。

### 0.5 证据纪律

* 结论以**落盘 JSON / trace / logcat** 为准，不用消息叙述当证据；
* 每个用例记录**命令原文 + 原始输出 + 产物路径 + 时间戳**；
* 区分「直接观测」与「推断」；
* mock 环境的结果必须标注 `mock 环境记录，非真机验证`，且与真机产物分目录存放。

---

## 1. 测试分级总览

| 级别 | 名称 | 需要什么 | 覆盖 | 是否本轮必做 |
|---|---|---|---|---|
| L1 | 静态检查 | Python 3.11+ | 语法/导入/CLI 装配 | 必做 |
| L2 | 单元测试 | Python + 可写 basetemp | T1–T6、T8–T13 的细粒度行为 | 必做（回归红线） |
| L3 | 本地三进程（mock） | 3 个终端 | 完整 run loop、ack 矩阵、超时、停止语义 | 必做 |
| L4 | 故障注入（mock） | 同 L3 | 坏帧、重传、落盘失败、断线 | 必做 |
| L5 | 真机联调 | KS8226 POS + Android 卡端 + adb | 与真内核/真 HCE 的端到端 | **阻塞**：需两端先升到 1.2 |
| L6 | 跨案例残留 | 同 L5 | §1.5 的 DELETE-then-ADD 覆盖语义 | **阻塞**：同 L5 |

L1–L4 只依赖本仓库；L5/L6 需要两端升级，见 §4。

---

## 2. L1 静态检查

### L1-1 字节编译

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -m py_compile `
  emv_host\protocol\messages.py emv_host\protocol\connection.py `
  emv_host\protocol\kernel_tlv.py emv_host\cases\models.py `
  emv_host\cases\validation.py emv_host\cases\loader.py `
  emv_host\store\run_store.py emv_host\appconfig.py emv_host\cli.py `
  emv_host\runner\session.py emv_host\runner\executor.py emv_host\web\api.py
```

预期：无输出、退出码 0。

### L1-2 导入与常量装配

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -c "import emv_host, emv_host.web.api, emv_host.runner.executor, emv_host.cases.loader; print('import ok')"
python -c "from emv_host.protocol.messages import PROTOCOL_VERSION_STRING as v, MSG_SESSION_ABORT as s, CONFIG_TYPE_POS_KERNEL as k; print(v, s, k)"
python -c "from emv_host.appconfig import AppConfig; c=AppConfig(); print(c.card_ack_timeout, c.pos_ack_timeout, c.stop_ack_timeout, c.abort_ack_timeout)"
```

预期：`1.2 session_abort pos_kernel_config`；`15.0 15.0 3.0 3.0`。

### L1-3 CLI 装配

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host --help
```

预期：退出码 0；`--help` 里能看到 `--card-ack-timeout` 与 `--pos-ack-timeout`（默认 15）；不出现 `Uvicorn running`（即没有真的起服务）。

实测（编写时）：`python -m emv_host --version` → `0.1.0`；`python -m emv_host run --help` → 列出 `--brand/--mode/--all/--case/--resume`。
注意：host 未启动时 `python -m emv_host status` 会以 `cannot reach server at 127.0.0.1:8000` **非零退出**，属正常行为，不要当缺陷。

仓库根目录下的 `card_simulator_host`（bash）、`card_simulator_host.bat`、`card_simulator_host.ps1` 是同一个入口的封装
（它们会 `cd` 到仓库根并设 `PYTHONPATH=<repo>/backend`）。因此本方案里的
`python -m emv_host <子命令>` 都可以在**仓库根**直接执行；v1.2 可用的子命令有
`serve / status / list / run / stop / abort / skip`。其中 `list` 会把案例库树打印到 stdout
（用于快速确认案例号与分组），`run` 需要先有正在运行的 host。

### L1-4 案例库冷加载

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -c "
from emv_host.cases import load_library
lib = load_library(r'E:\Work\ZT\card_simulator_host\cases')
print('brands:', sorted(lib.brands))
print('errors:', [i.message for i in lib.issues if i.severity=='error'])
print('visa cases:', lib.all_case_ids('visa','contactless'))
"
```

预期：7 个 brand；**errors 为空列表**；visa 的案例号包含 `VISA-CL-001..004` 与 `OPBD-001-*`。
> 若出现 error，先确认不是自己改坏了 `cases/`——本轮提交前后都应为 0 个 error。

---

## 3. L2 单元测试

### 3.1 运行

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -m pytest -v
```

若被 §0.3 的沙箱问题挡住：

```powershell
python -m pytest -v --basetemp=E:\Work\ZT\card_simulator_host\tmp\pt_$(Get-Random)
```

### 3.2 回归红线

以下用例**必须全部通过**，任一条失败即整轮不合格：

| 用例 | 断言要点 |
|---|---|
| `test_protocol_server.py::test_handshake_auth_and_different_device_rejected` | 握手 ack 的 `protocol_version == "1.2"` |
| `test_protocol_server.py::test_v11_protocol_version_rejected` | 广告 1.1 → `error{code:"protocol_mismatch"}`，且**不注册**该 client |
| `test_protocol_server.py::test_missing_protocol_version_rejected` | 不带 `protocol_version` → 同样被拒（v1.2 不再宽容） |
| `test_protocol_server.py::test_newer_protocol_version_accepted` | 广告 1.3 → 接受 |
| `test_protocol_server.py::test_bad_frames_are_skipped_until_five` | 第 1 个坏帧后仍能处理后续正常帧；第 5 个坏帧才断连 |
| `test_protocol_server.py::test_close_fails_pending_ack_with_connection_closed` | 连接关闭时 pending ack 抛 `ConnectionClosed`（不是 `CancelledError`） |
| `test_runner_result.py::test_result_is_persisted_before_ack` | ack 到达时 `results/<case>.json` 已存在，detail=`persisted` |
| `test_runner_result.py::test_persist_failure_acks_ok_false_and_does_not_resolve_waiter` | 落盘异常 → `ok=false` + `detail=persist_failed`，waiter 未 resolve |
| `test_runner_result.py::test_duplicate_msg_id_is_acked_duplicate` | 同 `msg_id` 重传 → detail=`duplicate`，`quarantine.jsonl` **不存在** |
| `test_runner_timeout.py::test_result_timeout_fails_case_stops_run_and_skips_remainder` | `failure_reason=result_timeout`；run `ABORTED` + `abort_reason=case_failed:C-01:result_timeout`；后续案例 `SKIPPED` + `note=run_stopped_after_failure:C-01`；两端各收到一次 `session_abort` |
| `test_runner_timeout.py::test_pos_offline_fails_case_instead_of_warning` | POS 离线 → `FAIL` + `pos_not_connected`（不再是 warn） |
| `test_runner_timeout.py::test_android_error_keeps_status_error_and_stops_run` | `status:error` → 案例 ERROR，`results[...].android_error == {code,message}` |
| `test_runner_timeout.py::test_kernel_config_is_pushed_before_pos_config` | `pos_kernel_config` 在 `pos_config` **之前**；帧内 `kernel_params.aid[0]` 以 `9F06` 开头 |
| `test_runner_timeout.py::test_case_level_kernel_params_override_brand` | 案例级 `kernel_params` 完全覆盖 brand 默认 |
| `test_loader_store.py::test_duplicate_case_id_rejected_and_first_kept` | 同号案例记 error；保留**独立文件**（`cases/<ID>.json`）那一份 |
| `test_loader_store.py::test_case_id_colliding_with_group_id_is_an_error` | `case_id` 与 `group_id` 互斥，报「互斥」 |
| `test_loader_store.py::test_atomic_write_leaves_no_tmp_and_reads_back` | 无 `*.tmp` 残留；覆盖后内容完整 |
| `test_loader_store.py::test_local_kernel_params_are_loaded_only_when_opted_in` | 默认**不读**仓库内 `kernel_params.json`；显式开关下才加载且 0 error（C3） |
| `test_api_cases.py::test_write_path_guard_refuses_escape` | `../` 路径 → HTTP 400 +「用例库之外」 |
| `test_api_cases.py::test_group_payload_cannot_carry_kernel_params` | 总案例带 `kernel_params` → 校验失败 |
| `test_api_cases.py::test_websocket_since_garbage_is_tolerated` | `/ws?since=abc` → 仍先收到 `kind=snapshot`，`seq=0` |
| `test_launch.py::test_pid_alive_true_for_self_false_for_dead_pid` | 本进程 pid 存活；死 pid 与 0/-1 为 False |

### 3.3 全套通过标准

* 通过数 ≥ 100（`test_frames` 等基础用例 + §3.2 明细）；
* **0 failed / 0 error**；
* 唯一允许的例外是 §0.3 的两类环境现象，且必须在报告里注明「环境」而非「缺陷」。

### 3.4 需要单独记录的边界断言

以下行为没有独立用例，但属于本轮验收点，请用一次性脚本确认（命令自拟，结论落盘）：

| 编号 | 断言 |
|---|---|
| U-1 | `ber_length(0x7F)=b"\x7f"`、`ber_length(0x80)=b"\x81\x80"`、`ber_length(248)=b"\x81\xf8"` |
| U-2 | 248 字节模数的 CAPK 编码后能被 `decode_tlv_map` 原样解回 |
| U-3 | 缺 `checksum` 时 `encode_capk` 自动算出 DF03，且等于 `SHA1(9F06‖9F22‖DF02‖DF04)` |
| U-4 | `DF05="PHWWIPQI"` 在**模型层**即被拒（`ValueError`），不进入后续校验 |
| U-5 | 9F06 为 9 字节 → 模型层拒绝；7/8 字节 → 接受 |
| U-6 | `validate_kernel_params(None, x)` 无任何 issue（= 该案例不下发内核参数） |
| U-7 | 空 `KernelParams().empty is True`，因此**不会**产生 `pos_kernel_config` 帧 |

---

## 4. L3 本地三进程（mock）

### 4.1 前置

```powershell
cd E:\Work\ZT\card_simulator_host
Remove-Item runs\.host.lock -ErrorAction SilentlyContinue   # 只在确认没有实例时执行
Get-Content tokens.json | ConvertFrom-Json | Format-List    # 确认 android/pos 两个 token
```

### 4.2 启动（三个终端，顺序固定）

终端 A（host）：

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host serve --trace-enabled --log-level info
```

预期日志（关键行，按顺序）：
`EMV test host v... starting: web http://127.0.0.1:8000, device TCP 0.0.0.0:9100`。

终端 B（卡端 mock）：

```powershell
cd E:\Work\ZT\card_simulator_host
python tools\mock_clients\mock_android.py --tap-delay 1.0
```

预期：`[android] connected, session android-...`（这一步证明 1.2 握手通过）。
**反证**：把 `mock_common.py` 里握手临时改成 `protocol_version="1.1"`，应看到
`handshake rejected: protocol_mismatch`——验证完立即改回，不要提交该改动。

终端 C（POS mock）：

```powershell
cd E:\Work\ZT\card_simulator_host
python tools\mock_clients\mock_pos.py --delay 2
```

### 4.3 用例 A：单案例 PASS（冒烟）

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
```

这是「离线批准」场景，与 mock POS 默认的 `--outcome approved_offline` 匹配。

观测点（三个终端的输出 + 落盘）：

| 顺序 | 观测 | 预期 |
|---|---|---|
| 1 | 卡端 | 收到 `card_config`，日志含 `trace=...`（v1.2 的 trace 开关改由 card_config 携带） |
| 2 | 卡端 | `status: ready` |
| 3 | 卡端 | `status: emulating`，随后 `trace: emitted 2 APDU pairs` |
| 4 | POS | `pos_config applied: 4 tag 参数`（VISA-CL-001 有 4 条） |
| 5 | POS | `case_started case=VISA-CL-001` |
| 6 | POS | `ack for transaction_result: ok=True detail=persisted` |
| 7 | 卡端 | 收到 `stop_emulation` 并 `status: stopped` |
| 8 | host | `[VISA-CL-001] final: PASS`；`run ... ended: FINISHED` |

落盘核对：

```powershell
cd E:\Work\ZT\card_simulator_host
$run = (Get-ChildItem runs -Recurse -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Write-Output $run
Get-Content "$run\run.json" | ConvertFrom-Json | Select-Object run_id,status,abort_reason
Get-Content "$run\results\VISA-CL-001.json" | ConvertFrom-Json | Select-Object case_id,status,attempt,attempts
Get-ChildItem "$run\trace" -ErrorAction SilentlyContinue
```

预期：`status=FINISHED`、`abort_reason` 为空、案例 `status=PASS`、`trace/VISA-CL-001.jsonl` 存在且含 2 对 APDU（约 4 行）。
**并且**：该目录下**不应**出现 `session_abort` 相关产物（正常结束不发 abort）。

> P-D5 修订：`attempt` 与 `attempts` **两字段并存**（F3 修复后）：`attempt` 为 0 基的「第几次尝试」，
> `attempts` 为 1 基的「尝试次数」。修复前真实结果记录只有 `attempt`，所以只读 `attempts` 会得到空值。
> 上一条命令已改为两者都读；`attempts` 是 `HistoryPage.tsx` 的读取字段，`attempt` 便于与日志对齐。

### 4.4 用例 B：离线拒付（`--outcome declined_offline`）

重启 POS mock：

```powershell
python tools\mock_clients\mock_pos.py --delay 2 --outcome declined_offline
```

跑 `VISA-CL-002`（期望 `resp_code=05 / declined / offline`）：

```powershell
python -m emv_host run --brand visa --mode contactless --case VISA-CL-002
```

预期：案例 **PASS**（断言与 mock 的拒付响应一致），run `FINISHED`。
> 该用例的价值：证明 PASS/FAIL 判定确实按 `expected_results` 走，而不是「只要收到结果就算过」。

### 4.5 用例 C：联机批准（`--outcome approved_online`）

```powershell
python tools\mock_clients\mock_pos.py --delay 2 --outcome approved_online
python -m emv_host run --brand visa --mode contactless --case VISA-CL-003
```

预期：`VISA-CL-003` PASS，`online=online` 断言满足。

### 4.6 用例 D：断言不符 → FAIL 且整轮停止

用 `declined_offline` 去跑 `VISA-CL-001`（期望 approved/offline）：

```powershell
python tools\mock_clients\mock_pos.py --delay 2 --outcome declined_offline
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
```

预期：

* 案例 `FAIL`，`results/VISA-CL-001.json` 里 `failure_reason == "assertion_failed"`；
* run `ABORTED`，`abort_reason` 形如 `case_failed:VISA-CL-001:assertion_failed`；
* 两端各收到 `session_abort`（两端终端日志应有 `session_abort reason=assertion_failed`；POS 的 reason 取自 `failure_reason`）。

### 4.7 用例 E：ack 超时（卡端不回 ack）

本轮改动新增了 `--card-ack-timeout` / `--pos-ack-timeout`，可把超时压到很小以便观察：

```powershell
# 终端 A：以极短 ack 预算启动
python -m emv_host serve --card-ack-timeout 2 --pos-ack-timeout 2 --log-level info
```

卡端：**不启动** `mock_android.py`，直接跑 run：

```powershell
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
```

预期：案例 `FAIL`，`failure_reason == "android_not_connected"`（设备未连接优先于 ack 超时）。

要真正触发 `card_config_ack_timeout`，需要一个「连上但收到 `card_config` 不回 ack」的对端。做法（二选一）：

* **推荐**：临时改一个 mock 副本（**不要动仓库里的 `mock_android.py`**）：把 `on_message` 里对 `config_push` 的 `send_ack` 去掉，另存为 `tmp\mock_android_silent.py`（同目录导入 `mock_common`），用它连上；
* 或用一个最小 TCP 脚本完成 1.2 握手后什么都不回。

预期：案例 `FAIL` + `failure_reason == "card_config_ack_timeout"`，run `ABORTED`，且卡端**不应**收到 `start_emulation`。

### 4.8 用例 F：POS 离线即 FAIL（Q8）

只启动卡端 mock，POS mock **不启动**：

```powershell
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
```

预期：案例 `FAIL` + `failure_reason == "pos_not_connected"`；run `ABORTED` +
`case_failed:VISA-CL-001:pos_not_connected`；卡端收到 `session_abort`（reason=`pos_not_connected`）。
> 这是与 v1.1 行为差异最大的一条：旧版这里只是 warn。

> P-D3 修订（以及 H2/TS-5 修复后的口径）：卡端**收不到** `stop_emulation` 的情况只应出现在
> 「失败发生在 `emulating` 之前」。本用例的 `pos_not_connected` 发生在步骤 3/2.5，此时**卡端已经
> emulating**，因此在 TS-5 修复后**必须**收到 `stop_emulation`（`status: stopped`）**并**收到
> `session_abort`。判据分档如下：
>
> | 失败发生在 | 卡端应收到 |
> |---|---|
> | `emulating` 之前（卡端未连接 / `card_config` 被拒 / ack 超时 / 就绪超时） | **仅** `session_abort` |
> | `emulating` 之后（`pos_kernel_config`/`pos_config`/`case_started` 失败、POS 离线、结果超时、断言 FAIL、手动 abort） | `stop_emulation`（一次）**且** `session_abort` |
>
> 修复前（H2 未修）第二档只发 `session_abort`——这正是 L6 跨案例残留的风险点。

### 4.9 用例 G：结果超时（POS 不回结果）

把 POS mock 的 `--delay` 设成**远大于案例 `timeout_seconds`** 的值（P-D7 修订：不要用
`--interactive`，它依赖控制台 stdin，在子进程/无 stdin 环境下 `input()` 会立刻 EOF 使 mock 异常退出）：

```powershell
python tools\mock_clients\mock_pos.py --delay 9999
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
```

`VISA-CL-001` 的 `timeout_seconds=180`，等待 180s（+ 余量）后：

预期：案例 `FAIL` + `failure_reason == "result_timeout"`；run `ABORTED`；
`abort_reason == case_failed:VISA-CL-001:result_timeout`；两端收到 `session_abort` reason=`result_timeout`。
> 若嫌 180s 太长，可临时用**单个案例 JSON 的副本**把 `timeout_seconds` 改成 15（`ge=10` 是模型下限），
> 放进 `cases/` 并跑完删除；不要改动原有案例文件。

### 4.10 用例 H：整轮停止后剩余案例为 SKIPPED（Q9）

用 `--all` 或选多个案例，先让第一个 FAIL，再检查其余：

```powershell
python tools\mock_clients\mock_pos.py --delay 2 --outcome declined_offline
python -m emv_host run --brand visa --mode contactless `
  --case VISA-CL-001 --case VISA-CL-002 --case VISA-CL-003
```
预期：

* `VISA-CL-001` = FAIL（断言不符）；
* `VISA-CL-002`、`VISA-CL-003` = **SKIPPED**，其 `results/*.json` 的
  `note == "run_stopped_after_failure:VISA-CL-001"`；
* run `ABORTED`；`GET /api/history/<run_id>` 的 `results` 三份齐全；
* **反证**：`VISA-CL-001` 之后不应再出现 `case_started`（POS 端日志确认）。

### 4.11 用例 I：`kind=group`（运行本组）

> 前端 T17 未实现，本轮只能用 API 直调（这是预期的）。

```powershell
# 先看有哪些组
python -m emv_host list | Select-String OPBD
# 用 REST 开跑（PowerShell）
$body = @{brand='visa'; mode='contactless'; kind='group'; group_id='OPBD-001'} | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/run -Method Post -Body $body -ContentType 'application/json'
```

预期：返回 `{ok:true, run_id:...}`；该 run 的 `queue` **只含** `OPBD-001` 的子案例（不含其它案例）。
反例：`group_id='NOPE-001'` → HTTP 404；不带 `group_id` → HTTP 400。

### 4.12 用例 J：控制命令

```powershell
# 在 4.9 的等待窗口内（POS 不回结果时）执行：
python -m emv_host abort
python -m emv_host status
```

预期：`abort` 返回成功；案例状态 `ABORTED`（**不是** FAIL），`failure_reason` 为空
（这验证 §2 的修正：手动 abort 不应继承上一个案例的失败原因）；
run `aborted`，abort_reason 走 `manual abort` 分支；两端仍收到 `session_abort`（reason=`manual_abort`）。

### 4.13 用例 K：正常关闭不发 session_abort

`VISA-CL-001` 正常跑完（用例 A）后，host 日志里**不应**出现
`session_abort broadcast`。这是「只在异常路径通知」的反向验证。

---

## 5. L4 故障注入

### 5.1 坏帧注入（§1.8）

用一个最小脚本向 host 的 9100 端口发「长度前缀合法、JSON 非法」的帧：

```python
# tmp\inject_badframe.py —— 需先完成 1.2 握手（参考 mock_common.py 的 connect）
import struct, json, socket
BAD = b"{not json"
def frame(b): return struct.pack(">I", len(b)) + b

s = socket.create_connection(("127.0.0.1", 9100))
# …此处用 mock_common 的握手逻辑发 handshake 并读 ack…
for i in range(4):
    s.sendall(frame(BAD))
    s.sendall(frame(json.dumps({"v":1,"type":"log","msg_id":f"ok{i}","message":f"alive{i}"}).encode()))
    # 每轮后发一个正常帧，证明连接仍存活
```

预期：

* 第 1–4 个坏帧：host 日志出现 `skipping bad frame (n/5)`，且**后续正常帧仍被处理**（host 日志能看到 `alive0..3`）；
* 第 5 个坏帧：`5 bad frames, dropping`，连接断开；
* 全程 host **不重启、不崩**。

### 5.2 `transaction_result` 重传（D2 去重）

在用例 A 的基础上，让 POS mock 把同一个 `transaction_result` 发两次。
做法：复制一份 `mock_pos.py` 到 `tmp\mock_pos_dup.py`，在报告结果处连续 `send` 两次同一帧（同 `msg_id`）。

预期：

* 第一次：ack `detail=persisted`；
* 第二次：ack `detail=duplicate`，host 日志 `duplicate transaction_result ignored`；
* `results/VISA-CL-001.json` 只写一次，**`quarantine.jsonl` 不存在**。

> P-D4 修订：去重窗口 = **当前 attempt**（`begin_attempt` 会重置 `judged_msg_ids`）。因此上表的
> 「`quarantine.jsonl` 不存在」**只对同一 attempt 内的重传成立**。若重传在**下一个案例已开始后**
> 才到达，`attributed=False` → 走 `_quarantine()`，此时预期是
> ack `detail=quarantined` **且** `quarantine.jsonl` 被写出（reason=`case_id mismatch`）。
> 这两条都是正确行为，判定时按「同一 attempt / 跨案例晚到」分别对照。

### 5.3 落盘失败（`persist_failed`）

让结果文件不可写：把 `runs\<日期>\<run_id>\results\` 目录设为只读，或用占用文件锁的方式。
推荐做法：run 进行中（WAIT_RESULT 阶段）执行

```powershell
$run = (Get-ChildItem runs -Recurse -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
attrib +R "$run\results"
```

再让 POS 上报结果。预期：

* POS 收到 `ok=false, detail=persist_failed`；
* host 日志 `结果落盘失败，未 ack 成功`；
* run **仍在等待**（不判 PASS/FAIL），直到超时后 `result_timeout`；
* 释放只读属性后重传同一结果（新 `msg_id`）应能正常落盘并判 PASS。
> 测试后务必 `attrib -R` 复原，否则后续轮次会持续失败。

### 5.4 断线（Q7）

* **卡端断线**：跑案例期间 Ctrl+C 掉 `mock_android.py` → 预期案例 `FAIL`、`failure_reason=android_disconnected`、run `ABORTED`；
* **POS 断线**：跑案例期间 Ctrl+C 掉 `mock_pos.py` → 预期案例 `FAIL`、`failure_reason=pos_disconnected`（v1.1 这里只是 warn）。

### 5.5 坏文件与重复 id（加载器容错）

在一个**临时 cases 根目录**里做（不要污染 `cases/`）：

```powershell
mkdir tmp\badlib\visa\contactless\cases
Copy-Item cases\visa\brand.json tmp\badlib\visa\brand.json
Set-Content tmp\badlib\visa\contactless\cases\BROKEN-001.json '{ not json'
Copy-Item cases\visa\contactless\cases\VISA-CL-001.json tmp\badlib\visa\contactless\cases\
cd backend; python -c "
from emv_host.cases import load_library
lib = load_library(r'E:\Work\ZT\card_simulator_host\tmp\badlib')
print('loaded:', lib.all_case_ids('visa','contactless'))
print('errors:', [i.message for i in lib.issues if i.severity=='error'])
"
```

预期：**不抛异常**；能正常列出 `VISA-CL-001`；`BROKEN-001` 以 error 记入 issues。

再验证重复 id：把同一个 `VISA-CL-001.json` 同时放到 `cases\` 根与某个组目录里，重载后应看到
「case_id 'VISA-CL-001' 重复」error，且**独立文件那一份**被保留。

### 5.6 原子写无残留

任一轮 L3 结束后：

```powershell
cd E:\Work\ZT\card_simulator_host
Get-ChildItem runs -Recurse -Filter *.tmp
```

预期：**空**。若出现 `.tmp`，记录该轮全部产物并上报为缺陷。

---

## 6. T-D0：POS 内核参数数据准备与校验

本轮 host 从**仓库外受控目录**读取内核参数（`--kernel-params-dir`，每 brand 一个 `<brand>.json`）。
C3 之后**不再**从仓库内 `cases/<brand>/kernel_params.json` 读取（该路径仅在显式开启兼容开关时才生效），
因为文件含 CAPK 模数。数据可用 `tools/export_kernel_params.py` 从 POS 资产导出一份副本到该目录；
未提供时该 brand 不下发内核参数，POS 用自带表。

### 6.1 数据来源

POS 资产（**只读，不得修改 POS 仓库**）：

```
E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params
```

格式：行式文本，`AID=` / `CAPK=` / `TERM=` 三个段落，每行是拼接 BER-TLV 的大写 hex。已核实：**AID 19 条、CAPK 29 条、TERM 1 条**；`DF03 = SHA1(9F06‖9F22‖DF02‖DF04)` 29/29 全部吻合。

### 6.2 生成 kernel params 文件

> **C3 修订（必读）**：该文件含 CAPK 模数（密钥材料），**不得放在仓库内**。正确位置是
> **仓库外受控目录**，通过 `--kernel-params-dir` 加载，文件名按 brand 命名为 `<brand>.json`：

```powershell
# 受控目录（仓库外；路径由统筹指定，示例为当前使用值）
$KP = "E:\Work\ZT\_secure\card_simulator_host\kernel_params"
New-Item -ItemType Directory -Force -Path $KP | Out-Null
# 之后把 <brand>.json 写到这里，例如 $KP\visa.json
```

**不要**写进 `cases/<brand>/kernel_params.json`：仓库内路径只在显式开启兼容开关时才会被读取
（`load_library(use_legacy_kernel_params=True)`，默认关闭），生产环境一律用仓库外目录。

`<brand>.json` 结构：

```json
{
  "aids": [
    {"aid": "A0000000031010",
     "fields": [{"tag": "DF01", "value": "01"}, {"tag": "9F08", "value": "0002"}]}
  ],
  "caps": [
    {"rid": "A000000003", "key_index": "01",
     "expiry": "20441231", "expiry_encoding": "ascii8",
     "hash_alg": 1, "key_alg": 1,
     "modulus": "<DF02 裸模数 hex>", "exponent": "03",
     "checksum": "<DF03 20 字节 hex>"}
  ],
  "term": "9F6604360000809F1A0201569F7A0101"
}
```

要点（易错项）：

* `aid` **不含** `9F06` 标签，只有值；`fields` 里**不得**再出现 `9F06`（会被拒绝）；
* `expiry` 是**规范日期** `YYYYMMDD`（`"20441231"`）字符串，**不是 hex**；
* `expiry_encoding` 四选一（默认 `ascii8`）：
  | 取值 | 线上 DF05 字节 | 示例 20441231 |
  |---|---|---|
  | `ascii8`（默认，现资产口径） | 8B ASCII | `3230343431323331` |
  | `bcd4` | 4B BCD（年2+月1+日1） | `20441231` |
  | `bcd3` | 3B BCD（YY+MM+DD） | `441231` |
  | `raw` | 原样透传 `expiry_raw` | 任意偶数长度 hex（仅 warning） |
  `bcd3`/`bcd4` 只能表达 2000–2099，否则加载期报 error；
* `checksum` 是 hex；留空则由 host 自动算（但**建议显式写**，便于人工核对）；
  DF03 与 DF05 编码**无关**（只覆盖 RID|索引|模数|指数）；
* `modulus` 是裸模数 hex，长度必须是 128/144/176/248 字节之一；
* 资产里 `RID=A000000333 / 9F22=08` 的 `DF05` 是 `PHWWIPQI`（异常样例）：**默认排除**，
  即生成后 **CAPK = 28 条**（资产 29 减去这 1 条）。只有在厂商确认必须原样下发时才使用
  `"expiry_encoding": "raw", "expiry_raw": "5048575749505149"`，且该校验只报 warning、绝不静默通过。

### 6.3 校验生成的文件

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -c "
import json
from emv_host.cases.models import KernelParams
from emv_host.cases.validation import validate_kernel_params
raw = json.load(open(r'E:\Work\ZT\_secure\card_simulator_host\kernel_params\visa.json', encoding='utf-8'))
kp = KernelParams.model_validate(raw)
rep = validate_kernel_params(kp, 'visa')
print('errors:', [i.message for i in rep.errors])
print('warnings:', [i.message for i in rep.warnings])
print('AID', len(kp.aids), 'CAPK', len(kp.caps), 'term', bool(kp.term))
"
```

预期（P-D2 修订后的口径）：`errors: []`；**AID 19 / CAPK 28 / TERM 1**。
> 资产本身是 29 条 CAPK，按 §6.2 的默认策略排除 `A000000333/08`（`DF05=PHWWIPQI`）后为 **28**，
> 这不是数据丢失，而是「不是日期的 DF05 默认不下发」。若改用 `raw` 放行则该条回归、计数回到 29，
> 并伴随一条 warning。

### 6.4 确认文件**不在**仓库内

```powershell
cd E:\Work\ZT\card_simulator_host
# 仓库内不应存在任何内核参数文件（含被 gitignore 的）
Get-ChildItem cases -Recurse -Filter kernel_params.json -ErrorAction SilentlyContinue
Test-Path "E:\Work\ZT\_secure\card_simulator_host\kernel_params\visa.json"   # 期望 True
# 模数不得出现在仓库任何位置（含 logs/、证据、快照）
git status --porcelain --untracked-files=all | Select-String -Pattern "kernel_params|_secure"   # 期望空
```

预期：第一条**无输出**（仓库内没有该文件）；第二条 `True`；第三条空。
> 修复轮已把原先在 `cases/visa/kernel_params.json` 的文件移出，并把 `logs/` 中三份含模数的
> 会话 dump 归档到仓库外；仓库内已核验 0 处模数残留。


预期：`git check-ignore` 输出命中 `cases/*/kernel_params.json` 规则；`git status` **不显示**该文件。
> 该文件含 CAPK 模数（密钥材料），**绝对不得**提交或外发。

### 6.5 下发路径的端到端验证（走 L3）

放好**仓库外**的 `<brand>.json` 后（见 §6.2），带 `--kernel-params-dir` 启动 host，按 §4.3 跑一例：

```powershell
python -m emv_host serve --trace-enabled --log-level info `
  --kernel-params-dir "E:\Work\ZT\_secure\card_simulator_host\kernel_params"
```

观测：

* POS mock 日志出现 `pos_kernel_config: aid=19 capk=28 term=yes`（数量以实际文件为准）；
* host 日志出现 `pos_kernel_config applied (...)`；
* **顺序**：POS 端 `pos_kernel_config` 的 ack **早于** `pos_config` 的 ack；
* host 落盘里不应出现 `pos_kernel_config_*` 类失败原因。

**反向**验证：不带 `--kernel-params-dir` 重启（或把外部文件临时改名），重跑 → POS 端**不应**出现
`pos_kernel_config`，案例仍应 PASS（= 不下发时 POS 用自带表）。

**失败**验证（P-D1 修订）：`pos_kernel_config_apply_failed` 需要**回 `ok=false` 的 POS 端**；
stock mock 没有内核、对任何参数都回 `ok=true`，因此该失败原因在 stock mock 上**不可达**。
两种做法：

* 用 reject 型 POS（仓库已有 `docs/test-report-v1.2/scripts/mock_pos_reject_kernel.py`，运行需把
  `tools/mock_clients` 加进 `PYTHONPATH`），预期案例 `FAIL` +
  `failure_reason == "pos_kernel_config_apply_failed"`、run `ABORTED`；
* 只验证 host 侧校验：把 `term` 改成**真·奇数长度** hex（如 `9F6604360000809`，15 字符）或非法 TLV。
  注意 **`9F660436000080`（14 字符 = 7 字节）是合法值**，别拿它当坏数据——它会被正常解析、
  案例照样 PASS（这正是 P-D1 记录的原方案错误）。奇数长度会在**加载期**被拦下并记 error issue，
  但 stock mock 下案例**仍会 PASS**（不下发就没人拒绝），所以这类判定要看 issues，而非案例状态。

---

## 7. L5/L6 真机联调（阻塞项）

### 7.1 阻塞条件（**未满足前不要开跑**）

v1.2 是**硬切换**：host 拒绝任何 `<1.2` 或缺失 `protocol_version` 的客户端。
本轮 P3 的对端通知文档尚未交付，两端也尚未升级，因此：

* **卡端（card_simulator）** 必须：握手带 `protocol_version:"1.2"`；不再期待 `device_config`；
  从 `card_config.trace_enabled` 读 trace 开关；新增 `session_abort` 必 ack；
  心跳/判死/ack 超时改用缺省 10s/30s/15s；坏帧单帧跳过不拆链。
* **POS 端（L2-SELFTEST-APP）** 必须：握手带 1.2；实现 `pos_kernel_config`
  （`updateAidParam` / `updateCAPKParam` / `setTerminalParam`）；`case_started` 必 ack；
  `transaction_result` 视为落盘确认（收到 ack 才算上报成功）；新增 `session_abort` 必 ack；
  运行时断线/超时一律按案例 FAIL 处理。

两端都升级并各自发 `[READY]` 后，才能进入 L5。**不要用「旧卡端 + 新 host」硬跑**，那只会得到
`protocol_mismatch`，不构成任何有效结论（可作为一次「硬切换生效」的验证记录，仅此而已）。

### 7.2 preflight 红线

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host status
```

要求：`devices.android.connected == true`、`devices.pos.connected == true`，且两端 `app_version` 与
统筹给定的期望版本一致。**任一不满足就不要开跑**（沿用 AGENTS.md §3 的轮次门控）。

> 注意：`/api/health` **不返回** `protocol_version`。判断 1.2 是否协商成功要看 host 日志的
> `handshake ok: ... protocol=1.2`，或两端各自的握手日志。

### 7.3 L5 必跑（历史回归红线）

| 案例 | 历史地位 |
|---|---|
| `VISA-CL-001` | 离线批准基线 |
| `VISA-CL-002` | 离线拒付基线 |
| `UPI-CL-002` | 银联离线拒付基线 |
| round#15 的 28/28 | 上一轮全量通过集合，须在新版本双端上重跑 |

执行工具：`python tools\joint_debug\run_cases.py --brand visa --mode contactless --case VISA-CL-001 ...`
> ⚠️ 该工具的 `--on-failure` 默认是 `continue`，而 v1.2 **一律停止**。报告时不要在归因里写
> 「因为 on_failure=continue 所以继续跑」——v1.2 下 FAIL 必然停轮，剩余为 SKIPPED。
> 该工具的 T20 适配（默认 `--expect-versions`、`failure_reason` 列）属 P2，本轮未做。

### 7.4 L6 必跑（跨案例 AID 覆盖）

这是本轮**语义变更**的核心验证（裁定：per-case AID 覆盖，POS 先 DELETE 再 ADD 同一 RID）：

1. 准备同一 RID（如 `A0000000031010`）但 `DF11`（或其它 AID 元素）**不同**的两个案例；
2. 先跑案例 1（内核写入 AID 体 A），再跑案例 2（内核写入 AID 体 B）；
3. 期望：案例 2 运行时内核实际使用的是**体 B**。

验证手段（双侧证据交叉，遵循 AGENTS.md §6）：

* host 侧：`pos_kernel_config` 两条的 `kernel_params.aid` hex 差异点；
* POS 侧：`EmvService.applyParams` 的日志（`内核参数灌装 ... existingAID=... AIDadded=... replaced=...`）；
* 内核侧：候选应用选择结果 / `retcode`。

**反例（本轮已知风险）**：若 POS 仍按 add-only + 「同 RID 已存在则跳过」（v1.1 行为），
则案例 2 会静默使用**旧 AID 体**，而 host 仍收到 `ok:true` → **误判**。
若观测到此现象，按下表记录并上报为**对端待实现项**（不是 host 缺陷）：

| 项 | 内容 |
|---|---|
| 现象 | 案例 2 的 AID 未生效 |
| 直接观测 | POS 日志 `AIDskipped=N`（而非 `replaced=N`） |
| 影响 | 跨案例 AID 残留 → 误判 |
| 需方 | POS 端实现「同 RID 先 DELETE 再 ADD」 |

### 7.5 L5/L6 产物要求

* 每案例：`runs/<日期>/<run_id>/run.json`、`results/<case>.json`、`quarantine.jsonl`（如有）、
  `trace/<case>.jsonl`、`host.log`；
* 双端 logcat（`tools/joint_debug` 会自动抓，或用
  `E:\Android\Sdk\platform-tools\adb.exe`，注意 Git Bash 下加 `MSYS_NO_PATHCONV=1`）；
* 报告必须分「mock 环境」与「真机环境」两份，不得混写。

---

## 8. 结果判定与缺陷分级

### 8.1 单个 L2/L3 用例的通过判据

必须**同时**满足：

1. 命令退出码为 0（或 API 返回预期状态码）；
2. 终端的**关键日志行**出现且顺序正确（见各用例的观测点表）；
3. 落盘 JSON 的字段值符合预期（`status` / `failure_reason` / `abort_reason` / `note`）；
4. 该用例声明的**反证**条件成立（例如「不应出现 case_started」「不应有 .tmp」「不应发 session_abort」）。

只满足第 1 条不算通过。

### 8.2 缺陷分级

| 级别 | 定义 | 例子 |
|---|---|---|
| **P0** | 数据损坏 / 崩溃 / 静默误判 | 案例应 FAIL 却判 PASS；`run.json` 损坏无法解析；host 进程崩溃 |
| **P1** | 语义与方案不符 | FAIL 后整轮未停；`session_abort` 未发；`pos_not_connected` 仍为 warn |
| **P2** | 可观测性/字段缺失 | `failure_reason` 未写入；`quarantine.jsonl` 未生成 |
| **P3** | 文案/日志/文档 | 日志中文乱码（**注意**：本机控制台代码页导致的乱码不算缺陷，需用 `chcp 65001` 或看日志文件复核） |

### 8.3 需明确排除的「伪缺陷」

* pytest 的 `WinError 5`（§0.3）；
* `CTRL_BREAK_EVENT` 相关失败（无控制台）；
* 终端中文乱码；
* `runs/` 下的空目录/残留 `pt_*`；
* 文档仍写 v1.1（P3 未做，属预期）；
* 前端仍用 `kind=selected` 跑组（T17 未做，属预期）。

---

## 9. 执行记录模板

每个用例填一行（或每条独立成节），产物与命令一并留档：

```markdown
### <用例号> <名称>
- 时间：YYYY-MM-DD HH:MM:SS
- 环境：mock / 真机 ；host 版本：<python -m emv_host --version>
- 命令：<原样粘贴>
- 期望：<从本方案抄>
- 实际：<原样粘贴关键输出>
- 落盘：<产物绝对路径>
- 判定：PASS / FAIL（缺陷级别：P0/P1/P2/P3）
- 备注：直接观测 / 推断
```

### 9.1 汇总表

| 级别 | 用例数 | 通过 | 失败 | 未执行 | 备注 |
|---|---|---|---|---|---|
| L1 | 4 | | | | |
| L2 | §3.2 全量 + §3.4 七项 | | | | |
| L3 | A–K（11） | | | | |
| L4 | 5.1–5.6（6） | | | | |
| T-D0 | 6.1–6.5（5） | | | | |
| L5 | 阻塞 | 0 | 0 | 全部 | 待两端升级 |
| L6 | 阻塞 | 0 | 0 | 全部 | 待两端升级 |

### 9.2 最终结论必须包含

1. L1–L4 与 T-D0 的通过率；
2. 是否出现 P0/P1 缺陷（有则给最小复现步骤）；
3. L5/L6 的阻塞状态与解除条件；
4. 明确声明「本轮结论不覆盖 P2/P3 未实现部分」。
