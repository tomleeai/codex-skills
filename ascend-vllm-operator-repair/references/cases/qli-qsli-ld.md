# 案例：950DT 的 QLI/QSLI 跨核 S2 拆分 / LD 合并超时

状态：**版本化规避修复，目标负载已验收**。已定位故障分支，未证明到具体底层同步指令。不要展开 LD 缩写为未核实的术语，也不要将它等同于模型 Decode。

## 指纹与适用条件

历史现场（2026-10-22）：8×Ascend950DT，CANN 9.2，npu-smi 25.6.rc1，torch 2.10.0+cpu，torch-npu 2.10.0.post7，vLLM 0.30.0+empty（ced6857afa0ea7b2e3f0846a62e1394e90f15607），vLLM-Ascend 0.30.0rc2（8fde6d4e335fe300e2b949aab0e31659cce0811d），cannbot-dsl 0.7.1，cannbot-arena-net 0.3.0。

DeepSeek V4.1 Flash、DP8/EP8/TP1、FP8/FP4 相关路径、PA_BBND packed cache，DSpark 5，FULL_DECODE_ONLY。索引头 32、逻辑 head_dim 128、index_topk 512；包含压缩比 2 的 QLI，以及压缩比 1、候选块容量 2048/块长 8 的 QLI/QSLI 路径。

这些是历史指纹，不是通用合法配置或下一次推荐值。先核实现有包是否仍包含相同消费者、ABI 和调度入口；不同平台/版本不得直接照搬。

## 识别证据与反例

典型表象：并发长输入时请求已发出但持续没有输出；协调进程打印 shared memory broadcast block 等待；worker 后来在 MoE `_preprocess` 的 `torch.repeat_interleave` / LocalScalarDenseNpu 同步点报 `507014`；HCCL watchdog 随后退出。

关键证据是设备 plog 的 `fault kernel_name=...QuantLightningIndexer...`，不是表象的 Python 栈。独立 QLI 4096 Query token / ratio 2，在没有加载模型或建立 HCCL 的进程中也超时；关闭 LD 后完成。仅修复 QLI 的完整模型仍卡住，随后独立 QSLI 4096 Query token / ratio 1 也复现 507014，关闭其 LD 后完成。两处都处理后完整负载通过。

`507014`、共享内存等待、MoE 栈或 watchdog 单独出现都不足以判定本案例。若实际故障 kernel 是其他算子、参数/metadata 越界、OOM、jemalloc 堆损坏或 HCCL 初始化失败，分别定位，不能拿该补丁代替分析。

## 修改位置与方式

先定位 **进程实际加载** 的 `ops` 包；历史上 NFS 工作目录 `deps/python` 只放 DSL 包，而实际 `ops` 在另一处本地安装目录。源码、构建输出和安装包可能不同。检查 `ops/__init__.py` 的原生目录注册与 metadata 的 `_aicpu/*.so` 相对路径，复制完整包以保持资源关系。

对独立副本中的两处 **PA_BBND 公共 metadata 入口** 添加 `ld=False`：

1. `ops/quant_lightning_indexer_metadata_dsl.py`，`quant_lightning_indexer_metadata()` 中的 `return build_metadata(...)`，`query_rows=rows` 附近。
2. `ops/quant_sparse_lightning_indexer_metadata_dsl.py`，`quant_sparse_lightning_indexer_metadata()` 中的 PA_BBND `return build_metadata(...)`，`sparse=True` 附近。

```python
    # Case qli-qsli-ld: keep complete query rows on a core, avoiding the observed LD path failure.
    # Remove after an upstream fix is validated with the same operator and service tests.
    return build_metadata(
        # 保留本函数原有的全部位置参数及关键字参数。
        # 在该调用已有参数列表中仅追加下一行，不重写调用或删掉候选、压缩等参数。
        ld=False,
    )
```

上面是插入意图示例，**不是可直接替换原调用的完整代码**。不要把示例覆盖到源码；用局部 patch 保留原参数，只插入 `ld=False` 和说明。原分核函数默认 `ld=True`；该选项使完整 Query 行/行组按核分配，避免同一行 S2 范围跨核拆分后的 LD 合并，仍保留多核并行。

历史未修复文件 SHA256（仅辅助核对该版本源码）：

| 文件 | SHA256 |
| --- | --- |
| QLI metadata | f432d48d24a2a0adf600bd6baf6b014ecb4fe319d52531128e6ff6d8e0ea8f38 |
| QSLI metadata | e88f8d417ce98daf523399e1e1216a30bfd17d8f60a1724c058c6abf911fa2d8 |

行号不是版本契约。hash 不同先读源码；可能只是注释变化，也可能已修复或 ABI 已变。用文本 diff、AST 和关键函数契约分辨，不能强行套补丁或重复插入关键字。不要改 `build_metadata` 的全局默认值，不修改 TND 分支或其他调用者。

在原环境 source 后添加带 `OPERATOR-FIX` 注释的 `PYTHONPATH` overlay 行；原 vLLM 参数全部保留。独立副本与原生库保持配套，本案例不需要为了这两处 Python 调度参数重编译全部原生算子。

## 独立数值测试

优先沿用当前包/同版本源码中的官方 QLI/QSLI 用例和参考；需要补测时，至少覆盖原失败的 4096 Query token、QLI ratio 2、QLI ratio 1 的候选输出和 QSLI ratio 1，以及首行、分块边界、尾行。按当前契约正确处理 ratio 1 时 `cmp_residual_k=None`，不把探针构造错误当成算子缺陷。

历史参考解包 MXFP4（低 nibble 在前）、按 E8M0 scale 解码，并模拟 BF16 QK/权重/累加语义；TopK 边界比较允许合法 ties。历史用例容差来自同版本官方参考（rtol=1/128，atol=2.5e-5），不是其他量化版本的通用容差。检查索引数量、唯一性、可见上下文范围、TopK 边界分数，以及候选块长度和编号合法性。仅输出 shape 正确不算通过。

独立失败探针曾用 `aclrtSetOpExecuteTimeOut(60)` 缩短等待，应核对当前 API 返回值且仅作用于该诊断进程；正式服务不带此设置。

## 历史验收与限制

原服务配置保持不变，4K 输入 / 2K 输出 / 并发 16 / 32 条正式请求，两轮 seed 1、10001 均零失败、每条输出 2048 token，prefix cache 命中率均 0%，测试后 health 200、同一实例 8 worker 存活。输出吞吐约 1371、1517 token/s，平均 TTFT 3948、1357 ms，TPOT 9.0、8.8 ms；另有两条基本生成检查通过。结果只证明该现场范围，不是完整模型精度或长期稳定性认证。

后续同包逻辑（只有注释不同）另有 4K/2K、并发 50、200 条的记录：200 成功、0 失败、409600 输出 token、0 cache hit、health 200，吞吐约 2692 token/s、平均 TTFT 2184 ms、TPOT 16.2 ms。负载和容器不同，不能把吞吐差异解释为修复代码更快。

曾发现本地安装包已原位补丁，而 NFS 构建源码仍默认 `ld=True`。重建/重装可能覆盖修复；需将补丁纳入受控构建或持续使用独立 overlay，记录哪份正在生效。旧 README、升级候选和早期失败 monitor 不替代最终原始日志与逐请求明细。

禁用 S2 跨核拆分可能降低少 Query、超长上下文下的并行度；最大模型长度参数保留不等于已实测其全部范围。恢复 LD 或升级包后重新验证，不能以“升级候选已准备”宣称根因已修好。
