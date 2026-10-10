# host 回复：卡端 MSD（磁条）支持确认

> 回复日期：**2026-10-10**　回复方：**host**（`card_simulator_host`）
> 依据：卡端《卡端-MSD-确认回复》`E:\Work\ZT\card_simulator\docs\卡端-MSD-确认回复.md`（2026-10-10，卡端 1.0.37 / vc39，commit `83dad86`）
> 结论：**A 采纳 / B 取 `CCC` 正式（`MSD_AC` 别名）/ C 接受 / D 以 §1 日志为准**；
> 　　　**本轮无卡端代码改动**，host 侧文档与案例已同步落地（见 §2）。

## 0. 逐项结论

| 项 | 卡端结论 | host 裁决 | host 动作 |
|---|---|---|---|
| 通知 §3 六个 `msd*` 字段 | CONFIRMED | **确认**，字段名/tag/格式/位置不变 | 无 |
| 通知 §2 五步行为 | CONFIRMED（与 §1 逐字节一致，除 ATC） | **确认**；ATC 值 host 不断言 | 无 |
| **A. `msdMode` 显式开关** | CHANGE，请 host 采纳 | **采纳** | Case07/Case08 档案各加 `"msdMode": "msd"`；host 校验器加值白名单（`msd`/`emv`/空，其它值 warning） |
| **B. 命令名** | 待裁决（建议 `CCC`） | **`CCC` 为正式名，`MSD_AC` 为等价别名（两者都接受）** | `docs/protocol.md` §8.4 注册表 7→8 名 + 命令映射表加 `80/2A/8E/80` 行；`validation.py`、前端命令列表、`N12_协商说明` 同步 |
| **C. MSD 下 PPSE 省 `50`** | 待确认 | **接受** | 通知 §5.1 记录该口径；host 侧案例不断言 PPSE 的 `50` |
| **D. 通知 §2.5 TLV 顺序** | 待确认/建议修订 | **以 §1 日志为准**（Case07 `9F36→DF4B`；Case08 `9F61→9F36→DF4B`） | 已修订通知 §2.5；并把顺序落成可判定规则（见 §1-D） |
| ATC 起始 `0001`（日志 `0105`） | 知悉 | **无需新增 `atcStart`** | host 断言全与 ATC 无关（Case07 DataRecord 不含 9F36；`card.gac.response` 用 regex 通配 ATC） |
| A-4 长度错回 `6700` | 实现口径 | **认可** | 记录在通知 §5.1 |
| A-5 不做读取限制 / 未配置记录 `6A83` | 实现口径 | **保持现状**（不要求固定记录） | 通知 §5.1 说明；案例库既有 warn 属预期 |
| §5.1 不算真实 CVC3 | 实现边界 | 知悉 | 本两案例不校验 CVC3（Case08 错误来自超 `DF21` 限额）；将来用带 MSD 密钥的配置跑其它 MSD 用例时另议 |

## 1. 逐项依据

- **A**：显式优于隐式；且你方保持"缺省=自动"（等于通知原口径），所以是**纯增量**、向后兼容。host 已验证档案 extras 会**原样下发**（`profile.model_dump()` 含全部 `msd*` 与 `msdMode`，未被过滤），故加字段不需要 host 代码改动。唯一加的护栏是校验器白名单（防拼写错，warning 级不阻断）。
- **B**：`80 2A 8E 80` 在 EMV Book 3 即 **COMPUTE CRYPTOGRAPHIC CHECKSUM**，POS 内核函数 `emvicc_compute_crypyographic_checksum()`、状态 `EMV_STA_CCC_COMPLETED` 都用 `CCC` → 用 `CCC` 可与 POS 日志逐行对照。host 侧注册表是"命令语义名"，因此 `CCC` 正式、`MSD_AC` 别名；**两个名字 host 都收**，你方已兼容，无需改码。
- **C**：与 §1 日志逐字节一致；ADF FCI 的 `50` 与 `aids[].label` 照常输出 → 终端取应用名不受影响；host 的 MSD 案例也不断言 PPSE 里的 `50`。**接受**。
- **D**：以 §1 日志（实测）为准，我通知 §2.5 的顺序写错了，已在通知中修订。同时把这条验收口径**变成可判定规则**：Case07/08 的 `card.gac.response` 由 `contains` 升级为 `regex`（pass 级）：
  - Case07 `^770B9F3602[0-9A-F]{4}DF4B030001009000$`
  - Case08 `^77109F6102EB859F3602[0-9A-F]{4}DF4B030000009000$`

  即"与 §1 逐字节一致（ATC 除外）"现在会被 host 直接判定：顺序或固定字段不符即 FAIL。

## 2. host 侧本轮落地（已完成）

| 文件 | 改动 |
|---|---|
| `cases/mastercard/contactless/cases/3G06-1241-Case07.json` / `-Case08.json` | profile 加 `"msdMode": "msd"`；`card.gac.response` 改 regex（锁顺序，ATC 通配）；notes 记录本轮协商结论 |
| `backend/emv_host/cases/validation.py` | `_NO_RESPONSE_COMMANDS` 增 `CCC`/`MSD_AC`；新增 `_MSD_MODES={msd,emv}` 与 `msdMode` 白名单告警 |
| `frontend/src/pages/AddCasePage.tsx` | 命令名列表增 `CCC`/`MSD_AC` |
| `docs/protocol.md` §8.4 | 注册表 7→8 名（`CCC` 正式 + `MSD_AC` 别名）；命令映射表加 `80/2A/8E/80` 行 |
| `docs/N12_协商说明_指令不响应.md` | 命令名注册表加 `CCC`（别名 `MSD_AC`）行 |
| `docs/change-notice-card-msd.md` | §2.5 改日志顺序（D）；§3 增 `msdMode` 行（A）；§4 增 regex 验收口径；§5.1 记录 A-4/A-5/C |
| `backend/tests/test_mastercard_msd_cases.py` | 12 例：`msdMode` 下发、extras 不过滤、regex 锁顺序、顺序错必 FAIL、白名单告警、`CCC` 注册 |

host 全套回归：**393 passed**；案例库加载 **0 error**（3G06 两案例各 1 条"后三项 AFL 未配置记录"的 warning，属预期）。

## 3. 卡端待办

1. 无需代码改动（A/B/C/D 均已在卡端实现或仅命名层面）；
2. 下一版可把 `CCC` 作为**首选显示名**（`MSD_AC` 保留为兼容别名），与 host 注册表一致；
3. 真机联调：按你方 §6.3 的清单，host 侧需要 **① 联调时间窗**（另行约定）、**② POS 侧日志回传**（host 会给出 `out.*` 与 `card.gac.mode`/`card.gac.response` 的判定结果）。
   > 联调前请确认：APK 已上机（你方当前仅"已构建未部署"）、`msdMode=msd` 档案已随 host 下发、卡端 APDU trace 恒开。

## 4. 版本

- 卡端：`1.0.37 (vc39)` commit `83dad86` —— host 已登记，未做版本比对以外的要求；
- host：本轮为**案例库 + 文档 + 本地校验**改动，**wire 协议零变化**（`card.gac.mode` 由 host 从 trace 的 `INS=2A` 推导）。
