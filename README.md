# EMV Test Host (card_simulator_host)

> 变更索引与审核入口：`docs/CHANGELOG.md`（协议层详见 `docs/protocol.md`）。
> **当前协议版本：v1.2（硬切换）**——两端必须同窗口升级；POS 侧口径见
> [docs/protocol-change-notice-v1.2.md](docs/protocol-change-notice-v1.2.md)。
>
> v1.2 要点速览（详见 `docs/protocol.md` §12）：
> * 握手必须上报 `protocol_version:"1.2"`，缺失或 `<1.2` 一律拒绝；
> * 删除 `device_config`（两端用缺省 心跳 10s / 判死 30s / ack 15s）；
> * 新增 `pos_kernel_config`（AID/CAPK/TERM/DRL/ExceptionFile 下发 POS）与 `session_abort`（异常收尾，双方必 ack）；
> * **任一案例 FAIL → 整轮立即停止**，剩余案例记为 `SKIPPED`；
> * POS 离线/断线、任一端 ack 超时 → 该案例 FAIL（v1.1 时代多为 warning）；
> * `transaction_result` 的 ack 表示「判定已落盘」（`persisted`/`duplicate`/`quarantined`）。

EMV 自动测试上位机：作为 TCP 服务端连接两个客户端——**安卓 NFC 卡片模拟应用**（安卓手机模拟真实银行卡）与**POS 交易应用**（真实 POS 机），编排测试、接收交易结果、自动判定用例、在浏览器中展示与控制。

```
┌──────────────┐   TCP :9100    ┌─────────────────────┐   HTTP/WS :8000   ┌─────────────┐
│ Android card │◄──────────────►│   EMV Test Host     │◄──────────────────►│ Browser UI  │
│ simulator    │  config/start/ │  (Python asyncio +  │  REST + WS events  │ React 明亮风格 │
└──────────────┘  status/ack    │   FastAPI)          │                    └─────────────┘
┌──────────────┐   TCP :9100    │  cases/  runs/      │
│ POS 交易应用  │◄──────────────►│  tokens.json        │
└──────────────┘  result 回传    └─────────────────────┘
```

* **编排模型**：后端只指挥安卓（下发卡片档案、启动/停止 NFC 模拟）；POS 由操作员手动操作，后端在每个用例开始前把 **内核参数（`config_push{pos_kernel_config}`）与 POS 交易参数（`config_push{pos_config}`）** 下发 POS（均需 ack），POS 交易应用在交易结束后自动回传 `transaction_result`，后端按 `attempt_id` 归属并判定。
* 协议细节见 [docs/protocol.md](docs/protocol.md)；**POS 端开发手册**（安卓 7.1.2）见 [docs/POS机应用开发手册.md](docs/POS机应用开发手册.md)。

## 快速开始

```bash
# 1. 后端依赖（Python 3.12+，已在 3.14 验证）
pip install fastapi uvicorn websockets pydantic

# 2. 前端构建（Node >= 20.19，已构建好的 dist 也可直接用）
cd frontend && npm install && npm run build && cd ..

# 3. 启动上位机（默认参数：Web 127.0.0.1:8000、设备 TCP :9100），四种方式任选
card_simulator_host                                  # ① cmd 启动器（仓库根，推荐）
powershell -NoProfile -ExecutionPolicy Bypass -File .\card_simulator_host.ps1
                                                     # ② PowerShell 启动器（Ctrl+C 无 cmd 询问）
python -m emv_host serve                             # ③ 仓库根/backend 目录直接运行（内置 shim，零安装）
pip install -e backend && card_simulator_host        # ④ 全局安装后任意目录可用
# 端口覆盖示例：
#    card_simulator_host --port 8123 --device-port 9123
# 全部参数见：
#    card_simulator_host --help

# 4. （可选）联调演示：两个 mock 客户端
python tools/mock_clients/mock_android.py
python tools/mock_clients/mock_pos.py --outcome approved_offline

# 5. 浏览器打开 http://127.0.0.1:8000
```

> 启动说明：
> * 仓库根的 `card_simulator_host.bat` / `card_simulator_host.ps1` / 根目录 shim 包 `emv_host/` 都会自动定位仓库与 `backend`，**在任何当前目录下行为一致**；
> * 路径参数（`--cases/--runs/--tokens/--static/--log-dir`）未指定时默认锚定仓库根（`<repo>/cases` 等）；显式传**相对路径**按当前目录解析，绝对路径原样使用；
> * `pip install -e backend` 安装全局命令与包，之后任意目录可直接 `card_simulator_host` / `python -m emv_host`（可选，非必需）；
> * PowerShell 执行策略若为 Restricted：用 `-ExecutionPolicy Bypass -File` 调用，或先 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

**退出**：按一次 `Ctrl+C` 即优雅退出（有正在运行的测试会先落盘为 ABORTED，重启后可断点续跑），1 秒内结束、退出码 0、无报错堆栈；启动失败（如 cases 目录缺失）只打印一行错误并退出码 1；上次崩溃残留的锁文件会在下次启动时自动识别为死进程锁并自愈，无需手动删除。

CLI 子命令（与浏览器按钮同一命令通道）：

```bash
python -m emv_host serve [--port 8000 --device-port 9100 --cases cases …]
python -m emv_host list                              # 列出用例库
python -m emv_host status                            # 服务健康与设备状态
python -m emv_host run --brand visa --mode contactless --all
python -m emv_host run --brand visa --mode contactless --case VISA-CL-001
python -m emv_host run --brand visa --mode contactless --resume
python -m emv_host stop | abort | skip
```

## 前端功能

明亮 Apple 风格界面（系统字体、大圆角、毛玻璃顶栏，字号随系统缩放）：

* **01 · 卡组入口**：按目录展示卡组（visa / mastercard / unionpay / jcb / amex / discover / pure，全部为非接 contactless 用例），显示用例数，点击进入；进入后为**用例选择**页——整轮测试 / 勾选批量 / 单个用例 / 断点续跑；可临时覆盖 suite 策略（失败即停/继续、重试次数）；点击用例查看 test plan、操作员指引与预期结果；
* **02 · 添加用例**：页面顶部区分两种形态——**可执行案例**（独立案例 / 选「所属总案例」即为子案例，写进该组目录并自动建议 `组号-NN` 编号）与**总案例（分组）**（只配置测试目的/条件/test plan/通过标准，**不配置** profile/交易参数/预期结果；新建即 `group.json`）。可执行案例表单：卡片档案从该卡组默认模板（`brand.json.card_template`）克隆并与案例**内嵌绑定**（profile.id 自动 = 案例号），可编辑 PAN/AID/PPSE/acBehavior 等卡片侧字段（**acBehavior 含「无」选项**——不设置首 GAC 行为、按终端 P1 应答，用于不支持 GAC 决策的卡组），完整 JSON 兜底；「交易参数」页签按卡组模板自动带出 **POS 交易参数（tag 列表，tag 可自行设定修改）**，只下发 POS；前端逐字段校验，服务端二次校验（案例号/总案例号唯一、内嵌档案存在且校验通过、仅非接、路径安全），错误逐条提示并自动定位到出错选项卡；添加后热重载立即生效，也可在「JSON 导入」选项卡粘贴整份用例/总案例 JSON（先经 `/api/cases/validate` 或 `/api/groups/validate` 校验再落盘）；
* **03 · 运行视图**：两端设备状态灯、当前用例与 attempt、操作员指引（金额/交易类型/PIN）、用例队列实时状态（PASS/FAIL/WARN/ABORTED/SKIPPED/INCONCLUSIVE）、终端风格滚动日志；三级控制按钮——立即中止 / 本用例后停 / 跳过当前用例；浏览器刷新不影响后端运行（WS 重连按事件序号重放）；
* **04 · 历史记录**：每次运行落盘 `runs/<日期>/<run_id>/`（run.json、逐用例结果、checkpoint、quarantine 迟到结果、断言明细），页面可回溯每一条断言的 实际 vs 预期。

## 用例库结构（`--cases` 目录）

```
cases/
  visa/
    brand.json                  # 卡组元信息 + suite 策略 + 环境假设
                                 # + card_template(默认卡片模板) + pos_params(默认POS参数)
    .gen_cases_manifest.json     # demo 生成器清单（见下）
    contactless/cases/*.json     # 独立用例（文件名 = case_id，直接可执行，自包含 profile）
    contactless/cases/<group_id>/    # 总案例分组：group.json（说明/test plan，无 profile）
                                   # + <group_id>-NN.json 子案例（可执行，各内嵌 profile）
```

> **demo 生成器安全约定**：`python tools/gen_cases.py` 只覆盖它自己生成的文件——
> 每卡组维护 `.gen_cases_manifest.json` 记录生成清单，重跑仅删除/重写清单内文件；
> **通过网页「添加用例」自建的用例不会被生成器删除**（避免误删，历史教训见 N12 轮次）。
>
> **新增演示卡组（2026-09）**：`jcb` / `amex`（American Express）/ `discover` /
> `pure` 各含 brand.json + 1 冒烟用例（`JCB/AMEX/DISC/PURE-CL-001`）。JCB/AMEX/
> Discover 为占位 AID/PAN，可在网页改真实值；**PURE**（Gemalto，PPSE=2PAY.SYS.DDF01）
> 使用发卡方数据：AID 列表 `D9999999991010/D9999999992020/D666666666/
> A0000002282010/A0000002802010/A0000007231010`、示例 PAN `6280706258024090`、
> tag57 track2 与 PAN 一致、`kernelId:"2D"`（PPSE 目录项 9F2A，透传供安卓侧）。

> **v2 绑定规则**：profile 与可执行案例一对一——每个案例 JSON **内嵌完整 CardProfile**
> （可再内嵌可选 keys）；总案例（group.json）不配置 profile。**不再有卡组级共享
> profiles/ 档案库**。安卓侧只接收卡片数据（AIP=82、CVM=8E、PPSE、PAN…都在 profile 内）；
> 交易参数（`transaction.pos_params`，带 tag）只下发 POS（`config_push{pos_config}`）。

`brand.json`：

```json
{
  "brand": "visa", "display_name": "Visa",
  "entry_modes": ["contactless"],
  "suite_policy": {"on_failure": "stop", "retry_count": 1},
  "card_template": { "id": "visa_card_template", "pan": "…", "aip": "5800", "afl": "…",
                     "acBehavior": "offline_approved", "records": [ … ] },
  "pos_params": [
    {"tag": "9F02", "name": "amount", "value": "100.00"},
    {"tag": "5F2A", "name": "currency", "value": "156"}
  ],
  "environment_assumptions": "在线用例需要测试收单主机/白名单"
}
```

用例文件（节选，完整模板见 `cases/visa/contactless/cases/VISA-CL-001.json`）：

```json
{
  "case_id": "VISA-CL-001",
  "title": "Visa contactless offline approved (TC)",
  "entry_mode": "contactless",
  "profile": { "id": "VISA-CL-001", "pan": "…", "aip": "5800", "afl": "…",
               "acBehavior": "offline_approved", "records": [ … ] },
  "transaction": {
    "operator_instructions": {"amount": "100.00", "currency": "CNY",
      "entry_mode": "contactless", "pin_hint": "none",
      "notes": "将手机贴到 POS 读卡器"},
    "expected_values": {"amount": "100.00", "currency": "156"},
    "pos_params": [ {"tag": "9F02", "name": "amount", "value": "100.00"} ]
  },
  "test_plan": [{"step": 1, "action": "…", "expect": "…"}],
  "timeout_seconds": 180,
  "retry_on_fail": 0,
  "expected_results": [
    {"path": "resp_code", "op": "eq", "expect": "00", "severity": "pass"},
    {"path": "outcome", "op": "eq", "expect": "approved"},
    {"path": "tags.gen_ac.9F26", "op": "present"}
  ]
}
```

* `operator_instructions` 只给操作员看；`expected_values` 只用于机器断言——POS 手输金额与案例不一致不会假失败。
* `pos_params`（POS 交易参数）：按 tag 下发 POS（每用例开始前 `config_push{pos_config}`，POS ack 后进入 case_started）；tag 为大写 hex（1–3 字节 EMV tag），可自行设定。
* `profile.noResponseCommands`（指令不响应，N12）：卡片对该指令**不发任何响应 APDU**（静默）；字符串=每次都静默，`{"command":"GPO","times":N}`=仅前 N 次静默；详见 docs/protocol.md §8.4。
* 断言操作符：`eq / neq / contains / prefix / regex / any_of / present / absent / range`；`severity: pass|warn`。
* 加载时校验：profile 必须内嵌（缺失即 error）；PAN Luhn / 有效期格式 / Track2 hex / AFL 指向记录存在 / AID 偶数 hex / pos_params tag 格式，问题在启动日志与「卡组入口」页显示；profile.id 与 case_id 不一致仅 warning。
* 改用例后无需重启：重新加载用例库即可（当前版本：重启服务加载）。

## 运行语义

* 同一时刻只允许一个运行会话（物理设备唯一）。
* 三个动词：`stop`=当前用例完成后停；`abort`=立即中止当前用例（ABORTED）；`resume`=从最近一次同卡组/方式的运行续跑未 PASS/WARN 的用例。
* **失败策略（v1.2 起）**：**任一案例 FAIL/ERROR → 整轮立即停止**，剩余队列逐条落盘为 `SKIPPED`
  （`note=run_stopped_after_failure:<case_id>`）。`suite_policy.on_failure=continue` 与请求里的
  `on_failure` **已不再生效**（字段保留仅为兼容）。
* 重试次数取 max(brand 策略, case 的 retry_on_fail)，每次重试新 `attempt_id`；但 FAIL 不再重试
  （FAIL 即停轮）。
* run 级 `abort_reason` 统一为 `case_failed:<case_id>:<failure_reason>`；案例级失败原因写在
  `results/<case>.json` 的 `failure_reason`（同时进 run 索引）。
* 写前检查点：每用例启动前落盘 `checkpoint.json`；服务重启时 RUNNING 运行标 INTERRUPTED、当前用例标 INCONCLUSIVE，可断点续跑。
* **设备断线（v1.2 起）**：**任一端**断开 → 当前用例 FAIL（`android_disconnected` /
  `pos_disconnected`）并停整轮（v1.1 时代 POS 断开只是告警）。
* **ack 纪律（v1.2 起）**：所有 host→端命令必 ack；超时或明确拒绝 → 该案例 FAIL 并给出具名原因
  （如 `pos_config_ack_timeout`、`card_ready_timeout`）。预算可用 `--card-ack-timeout` /
  `--pos-ack-timeout` 调整（默认 15s）。
* **异常收尾通知（v1.2 起）**：run 以 ABORTED/STOPPED/ERROR 结束时向卡端与 POS 各发一次
  `session_abort`（双方必 ack，3s best-effort）；正常跑完不发。
* 迟到/无主结果 → quarantine（不判定），历史详情可见；**同一 attempt 内**的重传按去重处理
  （ack `duplicate`，不写 quarantine）；跨案例晚到才进 quarantine。
* 卡端一旦上报 `emulating`，其后任何失败路径都会补发一次 `stop_emulation`（避免 HCE 会话跨案例残留）。

## 开发模式

```bash
# 终端 1：后端（路径默认锚定仓库根，任何目录下都可直接启动）
cd backend && python -m emv_host serve
# 终端 2：前端热更新
cd frontend && npm run dev     # http://localhost:5173, 自动代理 /api 与 /ws
```

### 内核参数（POS AID/CAPK/TERM/DRL/ExceptionFile）

**默认：仓库内、git 追踪**  `cases/<brand>/pos_configs/`，每个 `<ref>.json` 一个模块
（`index.json` 是清单）。分两类模块，case 自由配对：

* `CONF_*`：AID + CAPK + DRL(CRL) + ExceptionFile + TerminalParameter term；
* `PROFILE_*`：Profile tags term；合成 `term = CONF + PROFILE`。

case 用 `kernel_params_ref` 选 CONF、`terminal_profile_ref` 选 PROFILE。
brand 级默认集放在同目录的 `pos_configs/default.json`（case 不填 ref 时使用）。
> 该目录含 CAPK 模数（Amex Expresspay 测试密钥）；经用户裁决允许入库并 git 追踪。

**可选：外部覆盖**  `--kernel-params-dir <dir>` 优先于仓库内：

* `<dir>/<brand>.json` = brand 级默认集；`<dir>/<brand>/<ref>.json` = 命名库覆盖；
* 目录只覆盖部分 brand 不算错误；整个目录没有任何配置才报 error（v1.2.x）。

**生成 / 更新**

```bash
# Amex Expresspay XML -> 仓库内命名库（--flat 表示直接写进 --out-dir）
python tools/import_expresspay_xml.py \
  --config-dir "refernce/Expresspay 4.2.0/TerminalConfig" \
  --profile-dir "refernce/Expresspay 4.2.0/TerminalProfile" \
  --out-dir "cases/amex/pos_configs" --brand amex --flat --force

# 从 POS 资产导出 brand 默认集（当前仍写仓库外；visa 后续也会迁入）
python tools/export_kernel_params.py \
  --asset "E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params" \
  --out-dir "E:\Work\ZT\_secure\card_simulator_host\kernel_params" --brand visa
```

* `export_kernel_params.py` 默认排除 `DF05=PHWWIPQI` 那条（导出 AID 19 / CAPK 28 / TERM 1），
  确需下发用 `--include-raw-expiry`；
* DF05 支持 `ascii8`（默认）/`bcd4`/`bcd3`/`raw`；`DF03 = SHA1(9F06+9F22+DF02+DF04)` 与 DF05 编码无关；
* 旧单文件 `cases/<brand>/kernel_params.json` 仅在显式开启兼容开关时才读（默认关闭且被 .gitignore）；
* 参考 XML 目录 `refernce/` 仅本地保留（.gitignore）。

## 测试与工具

```bash
cd backend && python -m pytest          # 后端单测（184 个）
python tools/gen_cases.py cases         # 重新生成演示用例库（清单式覆盖，不删自建用例；
                                        #   清单项若逃逸 brand 目录会被跳过）
python tools/export_kernel_params.py --help   # 内核参数导出（写入仓库外目录）
python tools/import_expresspay_xml.py --help # Expresspay XML -> 命名 POS 配置库
                                             #   (--config-dir/--profile-dir/--out-dir/--brand)
python tools/mock_clients/mock_android.py  # 协议级 mock：安卓卡模拟（card_config 打印
                                          #   noResponseCommands 静默指令）
python tools/mock_clients/mock_pos.py     # 协议级 mock：POS（--outcome approved_offline
                                          #   | declined_offline | approved_online，--delay/--interactive）
```

> mock 的 `transaction_result` 会**等待 host 的落盘 ack** 并打印
> `ack ... detail=persisted|duplicate`；这一行为依赖 mock 的收发分离（reader task + 队列），
> 否则 on_message 内等待 ack 会自锁（v1.2 修复项 F1）。

### 联调驱动与报告（`tools/joint_debug/`）

```bash
# 驱动一轮：开跑前先预检"两端在线 + 版本一致"，不满足则拒绝开跑（退出码 2）
python tools/joint_debug/run_cases.py --all \
    --expect-versions pos=1.0.13,card=1.0.31 \
    --on-failure continue --retry 0 --settle 15

# 单个 brand / 单个用例
python tools/joint_debug/run_cases.py --brand visa --case VISA-CL-001

# 逐用例归因报告（读 run 汇总 + 结果 + 卡端 trace + 用例库 + 基线）
python tools/joint_debug/report.py --inputs logs/joint_debug/full15_1_visa.json \
    --baseline logs/joint_debug/run_20260909T114059.json \
    --out logs/joint_debug/full_run_15_report.md
```

* `run_cases.py`：每轮 run 自动抓双端 logcat（`logs/joint_debug/logcat/<stamp>-{pos,card}.log`）；
  长耗时用例（`UPI-CL-006/007`）自动排到队尾；`--settle` 控制 brand 之间的静默等待
  （默认 15s，等 POS 收尾上一笔，避免下一个 brand 首例被 ack 门控饿死）。
* `report.py`：归因规则集中在脚本顶部 `ATTRIBUTION_RULES`（按 case_id 维护）；报告含
  概览 / 按卡组汇总 / 逐用例表 / 归因明细（含 POS 诊断字段）；未上报诊断字段时如实标注。
* 手工改用例后可用 `POST /api/library/reload` 热重载，无需重启后端。


mock 客户端按 [protocol.md](docs/protocol.md) 独立实现，可作真实安卓/POS 开发的参照实现。

## 工程结构

```
backend/emv_host/
  protocol/   帧编解码、握手、心跳、TCP server
  cases/      用例模型、目录加载、档案校验
  judge/      键路径断言器
  runner/     单会话运行器：重试/中断/跳过/断点续跑
  store/      运行落盘、checkpoint、quarantine、历史索引
  web/        FastAPI + WebSocket 事件总线 + 静态托管
  cli.py      serve/status/run/stop/abort/skip/list
frontend/     React + TS + Vite（明亮 Apple 风格）
cases/        演示用例库（7 个演示卡组：visa/mastercard/unionpay 完整演示 + jcb/amex/discover/pure 骨架冒烟）
tools/        用例生成器 + mock 客户端
docs/         协议文档
```

## 安全说明

`tokens.json` 保存两个客户端的共享密钥，首次运行自动生成**开发用默认值**，正式使用务必更换。Web 只绑 127.0.0.1、无登录（本机工具）；设备协议无 TLS，适用于隔离的实验室网络。
