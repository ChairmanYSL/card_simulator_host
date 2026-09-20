# AGENTS.md — 联调约束规则

> 来源：2026-09-09/10 card_simulator_host 双端联调复盘（17/31 → 28/28）。
> 2026-09-14：跨会话通信基准切换为 session-link 插件（工具：`session_link_list` / `session_link_send` / `session_link_reply` / `session_link_status`）。
> 规则按"踩过的坑"固化，简洁优先；与本文件冲突时以本文件为准。

## 1. 绝对纪律
1.任何不能百分百确定的事情都应该先询问用户得到明确指示，绝对不能自己认为相关性达标就自己做主

## 2. 跨会话通信（session-link）
2. 对端必须在 `session_link_list` 中处于活跃状态才可达：每次通信前先列一次，用返回的 session id 定位对端（标题/cwd 仅作辅助识别），**不凭记忆沿用旧 id**（进程重启后重新枚举）。
3. 发送成功 ≠ 已送达：`session_link_send` 失败即**未送达**；成功也只代表已投递，关键消息带 `request_reply=true`，并用 `session_link_status` **核对落点**（`delivered-and-woken` / `delivered-silently` / `queued-offline` / `failed`），必要时要求对端 `session_link_reply` 回执。
4. 对端是**平级会话、不共享上下文**：消息必须自证身份（本会话 session id + 仓库路径）且自包含（背景、目标、验收口径一次讲清；是否需要回复写进正文）；长消息拆段、每段要求 ACK，问题清单**按端一次性下发**，不零散发。

## 3. 轮次门控（谁跑、何时跑）
5. 我是统筹，两端是执行方。**只在两端都明确 `[READY]` 后发起 run**；单方就绪不开跑。
6. 开跑前必须过 preflight：两端 `connected=true` 且 `app_version` 与 `--expect-versions` 一致，否则**拒绝开跑**（不发 run）。
7. 一轮只跑一次；run 期间要求两端不装/不重启；任一端改动前先 `[PAUSE-REQ]` → 我回 `[PAUSE-ACK]`。

## 4. 任务与消息优先级
8. 任何长任务（run、长分析、批量报告）**一律后台作业或子代理**；前台先用 `session_link_status` 清掉未读/未回执消息并与两端同步，再动别的。

## 5. 阻塞处理
9. 物理/环境阻塞（设备掉线、断电、无调试通道）**一次讲清恢复条件**；同一条件连续 ≥2 轮无变化即上报"需人工介入"，不逐轮重复播报。

## 6. 归因纪律
10. 只用**双侧证据交叉**：以卡端 logcat timestamp 为准（校准时钟偏移）+ POS 内核 `retcode`；**不用事件顺序推断因果**（field 事件同步、APDU 事件滞后）。
11. tag 分组 ≠ 来源：不得用 `tags.gen_ac` 出现 AC 推断"发生过 GENERATE AC"。
12. 同一错误码不沿用旧根因：每轮重新取证，标明"直接观测"还是"推断"。

## 7. 产物与文档
13. 脚本产物与人工分析**分文件**；重跑脚本前确认不会覆盖他人产物。
14. 状态易变项（协商项/版本行为）写**带时间戳的状态轨迹**；未在设备上落地前，不写"定稿/已实施"。
15. mock 与真机结果**分目录/分文件**并显式标注环境（如 `mock 环境记录，非真机验证`）。
16. 结论一律以落盘 JSON / trace / logcat 为准，不用消息叙述当证据。

## 8. 工具与设备操作
17. adb 统一用全路径 `E:\Android\Sdk\platform-tools\adb.exe`；设备异常先 `adb kill-server && adb start-server` 复核再判断。
18. `adb shell` 传设备路径必须 `MSYS_NO_PATHCONV=1`（防 Git Bash 路径转换）。
19. 判定 app 状态用 `dumpsys activity/package`，**不用 `ps | grep`**；卡端装完通常需在 Test 页点一次 Connect 才连上 host。

## 9. 意图理解
20. "暂停/停止"类指令按**最小范围**解释并确认；不擅自扩大（如不停 host 后台、不停只读子代理）。
21. 多用户/多会话场景先确认**裁决权归属**（验收方 vs 实现方），再决定是执行还是回传，避免结论反复改写。
