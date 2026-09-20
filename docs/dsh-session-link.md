# dsh 跨会话双工通信插件（session-link）操作手册

> 目标：在**同一个 dsh 进程**内，让两个会话互为对等端互发消息并互相反馈（A 发消息唤醒 B，B 执行后自行判断是否回投）。
> 插件形态为 **Host 单边**（无浏览器 UI、无需客户端授权），只注册模型工具 + 事件监听。
>
> **验证状态**：包已激活运行、4 个工具已带正确 schema 注册、`inject` 声明已生效（`ctx.agents.list()` 成功执行）；
> **实际投递、回投关联、离线补投、hop 累计这四项尚未跑过真机流程**，属于未验证项，详见第 10 节。

---

## 1. 当前实例标识

| 项 | 值 |
|---|---|
| pluginId | `seslnk-1` |
| 当前包 | `pkg-6`（`currentPackageId`，run-4，state = `running`） |
| 包总数 | 6（`pkg-1` … `pkg-5` 为修复轨迹，不可变，可回滚） |
| 注入 | `inject: ['tools', 'agents']` |
| 工具 | `session_link_list` / `session_link_send` / `session_link_reply` / `session_link_status` |

> ⚠️ 这是**运行时对象，不是磁盘文件**。关闭或重启 dsh 后插件消失，需要重新 `cordis_run` 到 `pkg-6`。
> 临时停用用 `cordis_stop`（保留包与版本指针）；`cordis_undefine` 是**永久删除**，不要在还需要回滚时使用。

---

## 2. 三条硬约束（来自源码，不是设计偏好）

这三条决定了实现形态，改动前必须遵守。

### 2.1 `source` 只能有 3 个键

`agent-message` 来源在 v2→v3 迁移校验器里是**严格键白名单**（`packages/session/session-format-v2-to-v3/src/payload.ts:110-121`，只认 `kind` / `form` / `senderSessionId`）。
自造 `kind` 或往 source 里加字段（例如 hop 计数器）都会让会话日志**无法迁移**。

⇒ 因此本插件的 relay 计数**不放在 source 里**，而是放在**给模型看的消息正文头部块**中（`[session-link … hop=n/m …]`），
由接收端 `parseRelay()` 回读。source 始终保持 `{kind:'agent-message', form:'relay', senderSessionId}` 三键不变。

### 2.2 `followup` 不等于落 `user/message`

`followup(msg)` 走 `send(msg,'next-turn',true)`（`packages/core/agent-loop/src/agent.ts:137-139`），
落盘事件是 **`agent/inbox/spliced`**（`packages/core/agent-loop/src/inbox.ts:238`）；
`user/message` 只在 turn **认领**该消息时才产生（`packages/core/agent-loop/src/agent.ts:373-377`）。

⇒ 所以入站账本必须监听 `session/event` 的 `agent/inbox/spliced`，**不能**等 `user/message`。
否则对端 idle、不跑 turn 时 `message_id` 永远进不了账本，`session_link_reply` 必然报 `unknown message_id`。

### 2.3 不能用 `subagents.sendMessage` 做通用中转

该服务只允许**直属父子**（continuable child）之间投递，其他目标抛 `UNAUTHORIZED`（`packages/subagent/subagent/src/continuation.ts:202-232`）；
`agentTeams.sendMessage` 只允许 Team 成员、且按 teammate **名字**寻址。
`send_message` 这个工具名也已被 `tool-subagent-control` 占用。

⇒ 正确做法是与 `packages/extensions/cordis-host-runner/src/index.ts:1023-1046` 保持一致：
`ctx.agents.get(sessionIdString)` 拿到 live agent，再 `followup` / `send`。

---

## 3. 两条能力边界

1. **只覆盖单进程。** `ctx.agents` 是**进程内**注册表。两个 dsh 进程（不同 `--port`、不同 profile）不共享它，
   本插件够不到——那需要 webhook / IPC。
2. **对端必须 live 才能立即投递。** `get()` 对未启动会话返回 `undefined`；本插件用内存队列 + `agent/created` 补投兜住，
   但**队列在进程重启后丢失**（沙箱内没有 fs 服务可用）。

---

## 4. 工具与参数

| 工具 | 参数 | 说明 |
|---|---|---|
| `session_link_list` | `include_self`（bool，可选） | 列出本进程活跃会话：sessionId、title、cwd、status、是否有 relay 排队 |
| `session_link_send` | `target`（必填）、`text`（必填）、`request_reply`（可选）、`silent`（可选） | 发消息给指定会话；默认**唤醒**对端开新 turn |
| `session_link_reply` | `message_id`（必填）、`text`（必填） | 回答收到的 relay，回到发送方。只接受**入站**的 message_id |
| `session_link_status` | `state`（可选，枚举）、`peer`（可选） | 查看 relay 账本 + 离线队列 |

唤醒语义：

- 默认 `followup` → 对端立刻跑 turn（**消耗对端额度**）。
- `silent: true` → `send(msg,'next-turn',false)`，只入队不唤醒，对端下次自己跑 turn 时才看到。

> 参数 schema 注意：沙箱 DSL 用**属性级 `required: true`**标注必填，可选属性**必须省略该键**；
> 写成 `required: false` 会被 guard 直接拒绝。

---

## 5. 操作流程

### 5.1 前提

在**同一个 GUI** 里打开**第二个会话**，两个会话都必须打开（live）。

### 5.2 确认插件在哪些会话里生效

在**每个**会话中各发一句：

> 用 session_link_list 看一下当前进程有哪些活跃会话

判断：

- 能调用 `session_link_list` → 该会话上下文里挂了插件，是**完整对等端**（能收、能自动回投）。
- 说没有这个工具 → 该会话上下文没挂。它**仍能收到** relay（投递是直接进它的 inbox，不依赖它有工具），
  但**不能自动化回投**，只能回文本给人看。

### 5.3 取 sessionId

在会话 A 跑 `session_link_list(include_self=true)`，输出每行开头就是 sessionId（形如 `session-xxxxxxxx-xxxx`），
带 `*` 的那行是 A 自己，抄下 B 的 ID。

### 5.4 跑主链路 A → B → A

在**会话 A** 发（把 ID 换成 B 的真实值）：

> 用 session_link_send 给 `session-<B的ID>` 发一条消息，request_reply=true。
> 内容是：请只回复"收到，我是B"，并用 session_link_reply 回投给 message_id。

预期：

1. A 的工具卡片返回 `session-link: delivered-and-woken -> session-<B> (message_id sl-…, hop 1/6)`。
2. **B 自动开始跑一个 turn**，其对话里出现一条用户消息，正文形如：
   `Agent session-<A> sent a message: [session-link from=… message_id=sl-… hop=1/6 reply=asked]` + 你发的内容，
   末尾带 `--- SESSION-LINK … --- END SESSION-LINK` 引导块。
3. B 调用 `session_link_reply(message_id="sl-…", text="收到，我是B")`。
4. **A 被唤醒**，收到回投消息（头部带 `replying_to=sl-… hop=2/6`）。

闭环成立。两边各自独立会话，历史与记忆分别持久化。

---

## 6. 验证账本

任意一边跑 `session_link_status`。输出含：`in`/`out` 方向、`message_id`、`peer=`、`state=`、`hop=n/6`、
是否已 `answered=`、`age=`。

> **唯一可信的送达依据是账本/对端日志，不是工具卡片的 `ok`。**
> 工具返回 `ok` 只代表已入对端 inbox，不代表对端已处理。

`state` 取值：

| state | 含义 |
|---|---|
| `delivered-and-woken` | 已入对端 inbox 并唤醒它跑 turn（正常路径） |
| `delivered-silently` | 已入队但**未唤醒**（`silent:true`），等对端自己下次跑 turn |
| `queued-offline` | 对端当前不 live，消息在内存队列，等它启动时补投 |
| `delivered` | 离线队列中的消息已补投成功 |
| `failed` | 投递抛错，见 `failure` 字段 |

---

## 7. 分支用例

| 用例 | 操作 | 预期 |
|---|---|---|
| 静默投递 | `silent=true` | 不唤醒；对端下次跑 turn 才看到 |
| 离线补投 | 发给**未打开**的会话 ID，再在 GUI 打开它 | 先 `queued-for-offline-session`；启动后自动收到，宿主终端打印 `session-link: flushed N queued relay(s) into session …` |
| hop 累计 | 连续来回回投 | 两边头部 `hop` 1→2→3… 累加到 `6/6`；到顶后 `reply` 报 `hop budget exhausted` |
| 重复抑制 | 2 秒内同 text 发同一目标 | 第二次 `duplicate relay suppressed` |
| 自投拒绝 | target 填自己的 ID | `refusing to relay to self` |
| 限流 | 1 分钟内对同一目标发 >12 条 | `relay budget exceeded` |
| 未知 id | `session_link_reply(message_id="sl-不存在")` | `unknown message_id` |
| 重复回投 | 对同一条入站消息 reply 两次 | 第二次 `already answered` |

> **hop 累计是重点回归项**：早期版本在入站侧硬编码 `hops: 1`，导致 hop 预算永远不累计、到顶保护形同虚设；
> `pkg-6` 已改为从 relay 头部回读。

---

## 8. 失败排查

| 现象 | 先查 |
|---|---|
| 工具列表没有 `session_link_*` | 该会话上下文没挂插件；用 `cordis_inspect_self` 确认 `seslnk-1` 仍 `running` 且 `currentPackageId = pkg-6`，否则重新 run |
| `target not live` 类失败 | 对端会话没打开；先 `session_link_list` 看它在不在列表 |
| 收到了但 `reply` 报 `unknown message_id` | 入站账本没登记（`agent/inbox/spliced` 监听未生效）；把宿主终端 `[cordis:…]` 日志贴出来 |
| 工具报 schema/参数错 | 报错原文；`required` 那套 DSL 很挑写法（见第 4 节注） |
| 重启 dsh 后工具消失 | 预期行为；重新 `cordis_run` 到 `pkg-6` |

---

## 9. 防风暴机制（5 层）

| 机制 | 参数 |
|---|---|
| 共享 hop 预算 | `DEFAULT_MAX_HOPS = 6`，跨回复累计，从头部回读 |
| 自投拒绝 | `target === caller` 直接拒 |
| 同文本去重 | `DEDUP_WINDOW_MS = 2000` |
| 单目标限流 | 60 秒内 ≤12 次 relay |
| 离线队列上限 | `MAX_PENDING_PER_TARGET = 50` |

**没有**采用 visited-set：hop 预算 + 限流已能封顶，额外状态收益不明显。
（仓库内本身也**不存在**可复用的消息级 hop/循环保护，见 `packages/subagent/subagent/src/depth.ts:28-36`、
`continuation-activation.ts:548-560`，那是创建期深度预算与血缘遍历，语义不同。）

---

## 10. 验证状态（取证纪律）

按本项目"标明直接观测还是推断"的规矩，逐项列清：

| 项 | 状态 | 依据 |
|---|---|---|
| 包成功激活 | **直接观测** | `cordis_inspect_self` → `state: running`，`currentPackageId: pkg-6`，`activeRun: run-4` |
| 4 个工具已注册且 schema 正确 | **直接观测** | `Tool.listTools` 返回 4 个工具，`session_link_send` 的 `required: ["target","text"]` 正确 |
| `inject: ['tools','agents']` 生效 | **直接观测** | `apply` 内 `ctx.agents.list()` 成功执行（否则 guard 会抛 "not injected"） |
| 事件监听注册成功 | **推断** | `apply` 未抛错即注册完成，但未触发过 |
| 实际 `followup` 投递 | **未验证** | 未实投（避免擅自唤醒其它会话） |
| 回投关联（`message_id` 闭环） | **未验证** | 同上 |
| 离线队列补投 | **未验证** | 同上 |
| hop 跨回复累计 | **未验证** | 同上；逻辑已修，需真机回归 |

跑完第 5～7 节后，把本表逐行改为"直接观测"并附证据（工具卡片原文 + 宿主终端日志）。

---

## 11. 安全边界（需人工决策）

**当前插件对出站消息没有任何人工闸门**：模型可以直接唤醒任意 live 会话，并消耗对端额度。

这与本仓库 `AGENTS.md` 的跨会话通信纪律（发送成功 ≠ 已送达、必须核对落点、轮次门控）存在张力——
那套纪律目前只能靠模型自觉。如需加闸门，可选：

- **白名单**：只允许对指定 peer 集合发送。
- **审批**：每次发送走 `userQuestions` 请人确认。
- **落盘排队**：需要沙箱外的通道（沙箱内无 fs 服务），复杂度显著上升。

---

## 12. 源码取证索引

| 事实 | 位置 |
|---|---|
| `agents.get(id)` 对非 live 返回 `undefined` | `packages/core/agent/src/index.ts:567` |
| `SessionId` 是**编译期**品牌，运行时就是普通 string | `packages/util/brand/src/index.ts:28-30`；`packages/core/session/src/types.ts:19,26-28` |
| `Agent` live 面（无 `runtime`、无 `dispose`） | `packages/core/agent/src/runtime-types.ts:163-242` |
| `followup` → `send(…,'next-turn',true)` | `packages/core/agent-loop/src/agent.ts:137-139` |
| inbox 只校验非空 id 且不重复 | `packages/core/agent-loop/src/inbox.ts:222-228` |
| `followup` 落 `agent/inbox/spliced` | `packages/core/agent-loop/src/inbox.ts:238`；事件结构 `packages/core/agent/src/types.ts:87-93` |
| `user/message` 仅在 turn 认领时产生 | `packages/core/agent-loop/src/agent.ts:373-377` |
| `Session.append` 自行 snapshot + deepFreeze（调用方无需预冻结） | `packages/core/session/src/index.ts:713,725` |
| `agent-message` source 严格 3 键 | `packages/session/session-format-v2-to-v3/src/payload.ts:110-121` |
| 已存在的最接近镜像实现 | `packages/extensions/cordis-host-runner/src/index.ts:1023-1046` |
| 沙箱 `harness.defineTool` / `registerTool` | `packages/extensions/cordis-host-runner/src/guard.ts:551-629` |
| 沙箱 ctx façade 与 inject 校验 | `packages/extensions/cordis-host-runner/src/guard.ts:709-781` |
| `subagents.sendMessage` 仅直属父子 | `packages/subagent/subagent/src/continuation.ts:202-232` |
| 创建期深度预算（语义不同的参照） | `packages/subagent/subagent/src/depth.ts:28-36` |
| 血缘遍历 visited-set（同上） | `packages/subagent/subagent/src/continuation-activation.ts:548-560` |

---

## 13. 相关文档

- `AGENTS.md` —— 跨会话通信与轮次门控纪律（本插件的人工约束来源）。
- `docs/vscode-navigation.md` —— 同类工具配置文档，格式参照。

---

## 14. 持久化形态（2026-09-17）

第 1 节说的"运行时对象，不是磁盘文件"已经不再是唯一形态。原动态包 `seslnk-1/pkg-6`
的源码已恢复并改写为**文件插件**，随 dsh 进程启动自动加载，不再需要 `cordis_run`。

| 项 | 值 |
|---|---|
| 插件包 | `C:\Users\bruce\.dsh\profiles\web\plugins\session-link\`（`index.js` + `package.json`） |
| 插件入口 | `C:\Users\bruce\.dsh\profiles\web\plugins\session-link\index.js`（ESM，`type: module`） |
| 挂载方式 | host 平面：`C:\Users\bruce\.dsh\profiles\web\cordis.patch.yml` 的 `- insert:` 行（`id: session-link`），`name` 为该绝对路径 |
| 生效范围 | 四个工具注册进 `tools` 注册表的**全局层**，**所有会话**可见（不再限于 `cordis` preset 会话） |
| 关闭方式 | 从 `cordis.patch.yml` 删除该 insert 行 |
| 生效时机 | dsh 进程重启后；`patchReload: live` 也会在补丁文件保存时热重载 |
| 配置 | 无。共享 hop 预算是模块内 `DEFAULT_MAX_HOPS = 6` 常量，`pkg-6` 本就不接受 config |

> ⚠️ **插件不能放在 `$DSH_HOME/plugins/`。** 首次落盘时曾放在那里，进程能跑但引入两个隐患；
> 正确位置是 `$DSH_HOME/profiles/` 之下，理由见 14.4。

### 14.1 改写边界（照搬，不是重写）

源码从 `session-6e92b260` 的多帧 zstd 会话日志逐帧解压、按 `tool/call` 记录提取，
`pkg-1` … `pkg-6` 六个版本全部可复现；落盘的是最终运行的 `pkg-6`。沙箱强制改动只有四处机械替换：

| 沙箱写法 | 文件插件写法 |
|---|---|
| `harness.defineTool(...)` | `import { defineTool } from '@deepseek-ai/dsh-tools'` |
| `harness.registerTool(ctx, tool)` | `ctx.tools.register(tool)` |
| IIFE `return { name, inject, apply }` | ESM `export const name` / `export const inject` / `export function apply` |
| 沙箱带 tag 的 `console` | 宿主 `console` |

DSL 完全一致，因为沙箱的 `harness.defineTool` **就是**真实的 `defineTool`：
`guard.ts:19` 从 `@deepseek-ai/dsh-tools` import 它，`guard.ts:567` 直接透传。
第 2–9 节的全部机制、wire 格式与防风暴参数原样保留。

### 14.2 验证状态（2026-09-17，重启后实测）

| 项 | 状态 | 依据 |
|---|---|---|
| 补丁文件 YAML 可解析、含 2 个 insert 行 | **直接观测** | js-yaml 解析 `cordis.patch.yml` |
| 模块是合法 ESM | **直接观测** | `node --check` 退出码 0 |
| 4 个工具定义能被真实 `defineTool` 接受 | **直接观测** | 逐一重放定义全部构造成功；`session_link_send` 的 `required=["target","text"]` 与第 10 节既有证据一致 |
| **插件随重启自动加载** | **直接观测** | 重启后 `session_link_list` 出现在本会话工具清单中，且调用返回真实数据 |
| **4 个工具在会话中出现** | **直接观测** | `session_link_list` 调用成功；`session_link_status` 返回 `(no relay records in this process)`（空账本，符合预期） |
| 本进程活跃会话枚举可用 | **直接观测** | `include_self=true` → 1 条（本会话）；`include_self=false` → 0 条 |
| 第 10 节的实投/回投/离线补投/hop 累计 | **仍未验证** | 与持久化无关，仍待真机回归 |

> 上面"活跃会话"是 `ctx.agents.list()` 的语义：**当前进程内存里 live 的 agent**，
> 不是磁盘上存在的会话。旧会话即使未归档，重启后也不在列表里，直到被打开。

### 14.3 相对动态形态的取舍

- **暴露面变大**：host 平面无 `isolate` realm，工具进全局层，普通 `standard` 会话也会看到
  `session_link_*`。这是路线选择的已知代价，不是缺陷。
- **离线队列仍不持久**：队列在内存、随进程重启丢失（第 3 节限制 2）。改成文件插件**治不好**，
  因为那需要沙箱外的落盘通道，与形态无关。
- **不再需要重新 `cordis_run`**：第 1 节与第 8 节末行"重启后重新 run 到 pkg-6"的操作已作废。
- 若将来希望按 preset 收敛暴露面，可改为用户 preset 形态：把该模块放进
  `.agent-presets/<id>/`，在副本合成里用相对路径 `./` 引用（`specifier.ts:45`
  把 `./` 归为 preset 相对路径）。

### 14.4 为什么必须打包在 `profiles/` 下（两条硬约束）

这两条是首次落盘位置错误的根因，**改动位置前必须遵守**：

1. **Node 的 parent-walk 决定了解析范围。** 模块自身 `import '@deepseek-ai/dsh-tools'`
   是按文件所在目录逐级向上找 `node_modules` 解析的；只有 `profiles/node_modules`
   携带整套安装闭包，而并列的 `$DSH_HOME/plugins/` **不在这条 walk 上**。
   （补丁里的裸标识符另说：`app-boot` 交给 loader 用 `bareModuleBaseUrl` 解析，
   所以补丁行写包名或绝对路径都行——受约束的是**模块内部自己的 import**。）
2. **`plugin-package-inventory-deepseek`（默认开启）会向上找最近的 `package.json`。**
   模块没有自己的 manifest 时，它会继承 profile 的 `package.json`——那个文件有 `name`
   却没有 `version`，于是每个请求都以 `REQUEST_EXTENSION` 失败。

因此正确形态是**一个 profile 本地包**：`plugins/session-link/` 目录内放入口
`index.js` 与自己的 `package.json`（`name` + `version` + `type: module` + `main`），
补丁行的 `name` 指向该入口的绝对路径。
