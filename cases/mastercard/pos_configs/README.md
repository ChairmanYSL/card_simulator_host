# Mastercard POS config library（CTEL2 v4.1）

本目录由 `tools/import_mastercard_ctel2.py` 生成，来源分工：

| 角色 | 来源 |
|---|---|
| 取值权威 | Mastercard Contactless Testing Environment（CTEL2）Version 4.1 Level 2 Reader Testing，2025-06-17：Table 14（交易数据默认值）、Table 15/16（默认配置数据）、Table 17/18（测试配置差异）、Appendix B（CA Public Key） |
| 结构模板 | 参考实现运行态 `MastercardHost/Config/Config/<ID>.json`（`AIDParam` 行按 `9F06` × `9C` × `9F2A` 组织 + `TermParam`）；`AID/<ID>.xml` 仅用于补齐运行态缺失的行 |

工具把每一条对参考数据的改动都记在 `index.json` 的 `import_report` 与 `doc_conflicts` 里，
可以逐条回溯到文档页码。

## 结构

* `aids[]`：每个 (AID, 交易类型, Kernel ID) 一条；`9C`（交易类型）与 `9F2A`（Kernel ID：02=Kernel 2、08=Kernel 8）
  是 `fields` 的前两项。同一 AID 按交易类型/Kernel 重复出现是文档的写法，
  host 判重已按 `(9F06, 9C, 9F2A)` 组合判重（`cases/validation.py`）。
* `caps[]`：Appendix B 的 12 个 CA 公钥，`DF06=01`、`DF07=01`、`DF05=20491231`（ascii8）。
  `DF03` 用文档打印值，并在导入时按 `SHA1(9F06‖9F22‖DF02‖DF04)` 复核。
* `drl[]` / `exception_file[]`：**空**（裁定：Mastercard CONF 不带 DRL；CTEL2 未定义 exception file）。
* `term`：全局终端参数（`TermParam` + 文档要求补入的项，如 `DF8117`）。
* `version`：`ctel2-4.1`（host 侧元数据，随 CONFIG 的 `VERSION` 下发）。

## 文档与参考实现的冲突

见 `index.json` 的 `doc_conflicts`，要点：

1. Table 17 对 `PPS_MStripe1` 的兜底句（"其余对象必须不存在"）与该表自身引用
   "Terminal/Reader Contactless floor limit" 矛盾；本库按"保留参考独有对象"的裁定保留
   MAG 侧的 `9F09/9F1B/9F1D/DF11-DF13/DF19/DF20/DF32-DF36`。
2. `PPS_MCnoDefault_1..5` 引用的 "Implementations" 表在 CTEL2 v4.1 中未印出；
   Always 类对象按文档删除，`EMV` 类跟随参考实现（同样删除）。
3. `PPS_MCnoDefault_2..5` 又把 `_1` 禁止的 Reader CTL / Reader CVM Required Limit 加回，
   按各配置 delta 处理。
4. `9F33`（Table 16 记 "Tag not present"）在参考实现带值处保留。

## 有意不导入

* DEDS（Data Exchange / DE-DS）配置族与 DEDS 专用对象（`DF8108`/`DF60`/`DF8109`/`DF62`/
  `DF810A`/`DF63`/`DF810D`/`DF8110`/`DF8112`/`FF8102`/`FF8103`/`DF8127`）。
* 文档未打印的 ICS 扩展 CAPK 索引：A000000004:05, A000000004:F7, A000000004:F8, A000000004:FA, B012345678:05, B012345678:F7, B012345678:F8。
* DRL（CTEL2 Table 13 的撤销列表）。

## 参考实现私有约定

`DF8308` / `DF8309` / `DF830A` 不是 EMVCo 内核数据对象，而是参考实现用来表达文档中
"某专有 tag 以零长度存在 / 必须不存在 / 以某值存在"的私有约定（如 `PPS_MC_prop` 的
`9F05`/`DF30`/`81`/`CF`），按"沿用参考写法"的裁定原样保留。

## 生成

```powershell
python tools/import_mastercard_ctel2.py `
    --ref-dir "E:/Work/VS_Project/MastercardHost/Config" `
    --out-dir cases/mastercard/pos_configs --brand mastercard --force
```
