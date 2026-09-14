# Host Protocol（已并入统一协议 v1.1）

> **本文档已废弃，仅保留为指向页（协商结论 N10）。**
> 统一设备协议请以 **[docs/protocol.md](protocol.md)（v1.1，唯一权威）**为准，
> 该文档已完整吸收本页的安卓侧细节：
>
> * 验证哲学：POS `transaction_result` 判定权威；APDU 流降级为诊断 trace（Phase A）；
> * 协议层字段 snake_case；`CardProfile`/`keys` 负载 schema（§8/§9）；
> * 安卓状态机 ready/emulating/stopped/error 时序与 error 码表（§6.4）；
> * trace 结构化格式、`ac_crypto` 真假密文规则、device_config 解释、watchdog（§5）。
>
> 协商过程：H1–H10 / O1–O7 / N1–N10 已全部定稿并落改，历史版本见 git。
