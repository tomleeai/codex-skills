# PR 说明与 QSA 案例

## PR 正文

沿用目标仓库模板。填写实证字段，尚无证据的项目列为待验证。

| 部分 | 写入的内容 |
| --- | --- |
| 触发现场 | 镜像与源码版本、NPU/模型/量化、影响复现的拓扑与参数、失败阶段及预期行为。附短报错或关键指标和最小复现。说明哪些版本和输入确实复现过。 |
| 改动范围 | 该 PR 修复的具体分支和受影响的调用者，如何保持既有 fast path、校验或数据布局。列相关 issue/PR、base 分支和 commit，解释 release/main 的差异。 |
| 回归测试 | 目标 base 的行为和 PR head 的结果。记录测试命令、断言范围、正常路径和边界。CPU stub、静态检查和硬件测试分别列出。 |
| 部署实测 | 记录实际运行的 commit 或补丁哈希、共同前置补丁、P/D/Proxy 或等价拓扑，以及补丁前失败和补丁后通过的阶段。按实际完成情况给请求长度、并发、成功数、真实输出 token、关键计数和日志证据。 |
| 待验证 | 如 PR head 的 NPU 端到端、现有快路径/精度或图捕获与 replay。只写本 PR 相关项，并注明是否已有可复用现场。 |

在长输入场景，单请求验证跨 P/D 的通路；如果缺陷涉及持续 Decode、调度、跨 rank 或池化，增加能覆盖该路径的实际并发和输出长度。约定零前缀复用时，根据两端正式窗口的指标差值核对实际命中，将同一请求内的 P→D KV 交接单独说明。

## Qwen3.8-Flash-Next 基础 PD 的两个 PR

现场为双机 Ascend 950DT、每侧 8 卡、TP4×DP2、EP、`MooncakeConnectorV2`、MRV2、text-only 和 MTP3，镜像内 vLLM-Ascend 为 `62fe4cde6`。

- **Raw-key 页步长。** 权重加载后，混合 KV 初始化拒绝了 2048 字节逻辑 raw-key 视图所在的 2240 字节物理页。回归验证不同宽度同域时按物理步长寻址、相邻页写入隔离，并保留压缩页和超宽视图的校验。说明模型已在某 release 声明支持却仍能触发时，应给具体布局条件。没有历史发布验收的配置和 per-layer 规格时，将差异列为未知。[vLLM-Ascend #21945](https://github.com/vllm-project/vllm-ascend/pull/21945)
- **QSA Norm + RoPE fallback。** 初始化过后，Prefill warmup 的普通 RoPE 输入在融合资格检查处抛出异常。review 要求验证仍满足 ABI 的 MRoPE 走融合，普通 RoPE 走 fallback，并询问为何 MTP 分支也需要换实现。若需改 MTP，补原实现对照的数值等价证据。该 release PR 还被要求交代 main 是否受影响，并在精确 PR 提交上覆盖原失败的预热、完整 forward 和 MTP 图捕获/replay。[vLLM-Ascend #21979](https://github.com/vllm-project/vllm-ascend/pull/21979)

上述现场补丁组合通过了 `131072 输入 × 1 输出` 的 Proxy 功能请求，恢复后的基础 PD 又跑完 `128K+1K` 零前缀复用用例。两条 PR 列出的目标分支 CPU 回归和现场镜像 NPU 结果来自不同版本。写结论时注明共同补丁及实测版本，并按需要补精确 head 的 NPU 验证。[基础 PD 性能案例](https://github.com/tomleeai/codex-skills/blob/main/knowledge-base/vllm-performance/cases/case-qwen38-flash-next-650e-pd-128k-20261116.json)
