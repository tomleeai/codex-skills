# Qwen3.8-Flash-Next：PD + PP + P KV offload + MemCache layerwise + Mooncake layer Push

更新：2026-11-19，Asia/Shanghai。此文件为项目持久记忆，依据本会话、交付脚本和原始结果整理；本次仅离线归档，未重新检查服务器实时状态。不保存密码或其他认证材料。

## 用户最新意图与停止状态

- 用户要求记住本服务组合；忽略此前“追赶/比较基础 PD 分离性能”的优化目标。历史数据保留，但基础 PD 不再作为当前目标、验收门槛或后续自动任务。
- 用户此前明确要求“暂时停止我们的实验，保留现场”。此要求继续有效；本次整理记忆不构成恢复服务、续跑 32K 候选或重新压测的授权。
- 最后现场检查：北京时间约 2026-11-19 09:36，P/D 被外部终止；随后其他容器启动另一工作负载。Meta/Proxy 保留，自有压测和优化控制器均已退出，无排队续测。该状态是当时快照，不能当成未来实时状态。
- 顶层 `run_p.sh` 已恢复已通过的 16384 token 预算。32K 候选初始化期间被中断，未就绪、未发压测、没有性能成绩，不能判定为配置失败、显存不足或性能筛除。启动者/终止者身份未确认。
- 用户以后明确要求恢复时，先核对资源归属、真实进程身份和现场文件。候选尝试仅是历史断点；不得默认把 32K 列为必跑任务，也不得终止其他容器服务。

## 服务身份与运行依赖

| 项目 | 已确认内容 |
| --- | --- |
| 规范名称 | `qwen3.8_flash_next + pd + pp + p-kv_offload + memcache_layerwise + mooncake_layer_push` |
| 模型/服务名 | Qwen3.8-Flash-Next W4A8 MXFP；`qwen3.8-flash-next` |
| 权重 | `<MODEL_DIR>` |
| 硬件 | 两台 Ascend950DT / Atlas 650E FM16，每台 8 卡，设备 0–7 |
| 容器 | 两端均使用 `Qwen3.8-Flash-Next-wcy-r112` |
| 镜像 ID | `sha256:a142c5e748ee0ae64dd423efc41cfea5723026a285075ba027ca58c57af6470a` |
| vLLM | 0.33.0，源码基线 `4ba787ec9959e566d3b587a796dff07673324da5`，含现场适配；不能将 commit 单独当成完整运行源码指纹 |
| vLLM-Ascend | 源码基线 `9005a22a862a95d52025b70fb2bff8eed8a5a5e3`，实际依赖项目源码快照 |
| 其余版本 | torch 2.10.0+cpu；torch-npu 2.10.0.post7；MemCache/MemFabric 1.3.0 |

远端工作目录：
`<SERVICE_DIR>`

原参考脚本位于同级 `pd+pp+p-dcp+p-kv_offload+memcache_layerwise/scripts`。当前组合以新工作目录顶层真实文件为入口。`/scripts` 仍指向旧实验，不能用 `/scripts/run_p.sh` 启动本服务。

`env.sh` 中 `select_runtime p|d` 选择本项目 `.runtime/p/core`、`.runtime/p/ascend` 或 `.runtime/d/core`、`.runtime/d/ascend`，再设置 PYTHONPATH。P/D 快照不同；不要用镜像默认安装或旧项目目录替代。审阅下载包不含 `.runtime`，服务器保存有该依赖；搬迁服务必须另带源码快照并核验哈希。镜像相同不能证明实际加载代码相同；不据此认定其他 QSA PR 已合入。

## 角色、链路和关键配置

| 角色 | 主机 | 端口/入口 |
| --- | --- | --- |
| P | P_HOST | HTTP8357，KV33470；`run_p.sh` |
| D | D_HOST | HTTP8358，KV33480；`run_d.sh` |
| Proxy | P_HOST | HTTP8356；`proxy.sh` → `prefill_proxy.py` → 本项目 `layerwise_proxy.py` |
| Meta | P_HOST | 25450 服务、25451 配置、25452 指标配置；`run_meta.sh` |

两侧均 TP4×PP2×DP1、EP 开启，48 层按 24+24 切分，DCP 关闭（`--decode-context-parallel-size 1`）。两侧均开启 MTP3：`method=qwen3_5_mtp`、`num_speculative_tokens=3`、`enforce_eager=true`；运行时方法名可规范化为 `mtp`。D 的 draft/accepted 实际计数增长已验证。

共用参数：max_model_len=143360，max_num_seqs=16，block_size=512，gpu_memory_utilization=0.9，chunked prefill 与 prefix caching 开启，mamba_cache_mode=align、mamba_ssm_cache_dtype=bfloat16，量化 ascend。测试零前缀通过新 seed 和实测计数控制，并非关闭 prefix caching。

| P | D |
| --- | --- |
| token 预算16384；eager / 图 NONE；async scheduling | token 预算4096；FULL_DECODE_ONLY；no-async scheduling |
| shared_expert_dp、flashcomm1、CPU binding 开启；shared expert overlap=true | shared_expert_dp、flashcomm1、CPU binding 开启；shared expert overlap=false |
| fused_mc2=0；fuse_norm_quant=false | fused_mc2=0；fuse_norm_quant=false；npugraph_ex=true；static/super kernel=false；recompute scheduler=true |

P 使用 MultiConnector：

- `MooncakeConnectorV2`，producer，`use_layerwise=true`，负责 P→D layerwise Push。
- `AscendStoreConnector`，producer，backend=`memcache`，`use_layerwise=true`，lookup 标识 `qwen38_pdpp_p`，负责 P 侧层间 KV 复用与外部池存取。

D 使用 `MooncakeConnectorV2` consumer、`use_layerwise=true`。用户所称 `mooncake_layer_push` 在本服务中对应上述 V2 layerwise Push 路径；不要擅自替换成另一种名为 `MooncakePushConnector` 的连接器。D-first layerwise Proxy 配合该链路，包装器记录 `FM16_PREFILL` 区间。

P 层式布局：`layerwise_num_shared_buffers=4`、`layerwise_prefetch_layers=3`、每个 PP 段局部独立层 `[0,3,7,11,15,19,23]`。QSA 独立驻留，其他层继续复用和 offload；不是所有层关闭卸载。`engram-config.cpu_offload=true, dp_shared_memory=false` 是原有 Engram 权重设置，与 P KV offload 含义不同。

MemCache 配置：`device_uboe` DRAM，初始每 rank 32GiB，最大配置每 rank 1024GB，world_size=64，storage.enabled=false（无 SSD）。这里 world_size 不是 vLLM rank 数；8 rank 初始池合计256GiB，不将最大配置称为实际已分配容量。`pool_env.sh` 从镜像包定位库目录并设置 LD_LIBRARY_PATH。

通信沿用现场配置：网卡 enp34s0f1、HCCL_ALGO=level0:fullmesh、HCCL_OP_EXPANSION_MODE=AIV、HCCL_BUFFSIZE=1024；P HCCL 61600–61699，D 61700–61799。启用 V2 model runner、`VLLM_QWEN38_ALLOW_PLE_PP=1`。配置值不单独证明物理拓扑。

## 已完成的业务验证

使用真实 AISBench，两个 case 顺序执行、每轮请求数为4×并发，新 seed，预热排除。实际 chat 输入131123–131127 token；输出逐条严格为1或1024。吞吐取 AISBench Common Metric，非逐请求吞吐平均。

| case | 三轮协议/完整数 | 吞吐逐轮 token/s | 三轮均值 token/s | TTFT P90逐轮范围 | TPOT P90逐轮范围 |
| --- | --- | --- | --- | --- | --- |
| 128K+1，直连P8357 | C2，每轮8；24/24 | 输入8346.9082、8346.9528、8345.9151 | 输入8346.592033333332 | 31.9640–31.9669秒 | 不适用 |
| 128K+1024，Proxy8356，prefix=0 | C4，每轮16；48/48 | 输出59.9276、61.2583、59.5123 | 输出60.232733333333336 | 49.6315–51.0194秒 | 19.2–24.1ms |

端到端三轮 TTFT 平均值均值43.7721666667秒、TPOT 平均值均值17.6333333333ms。分位数范围不是把全部请求合并重算的 P90。

后续在原16K热服务上又完成独立新 seed 单轮：P C2×8输入8344.9078 token/s，TTFT P90=31.9774秒；PD C4×16输出61.0307 token/s，TTFT P90=49.3078秒、TPOT P90=23.3ms，共24/24。这两轮独立保存，不混进上述三轮正式均值。

所有有效轮次 P HBM/外部前缀命中均为0；D 本地 HBM 命中为0，D 外部命中属于当次 P→D Push，不能解释为跨请求前缀复用。有限批次已通过长度、成功数和缓存计数验证；未独立验证精度或无限期稳定性，也未证明旧 D 长跑停滞问题彻底解决。等待 copy/event 的栈位置不足以定位故障 kernel。

## 启动与复测的已知约束

本节是手工恢复知识，不是待执行操作。恢复需用户明确指示，并重新检查两端卡/端口归属。

1. 进入指定容器和完整工作目录；`check_runtime.sh p|d`、入口 `--plan` 可用于静态核对。
2. Meta 先监听，再分别启动 P/D；初始化耗时数分钟，等待模型及 MTP 加载完成。P/D `/health` 就绪后启动 Proxy；Proxy 健康路径为 `/healthcheck`。
3. 入口为前台 `run_meta.sh`、`run_p.sh`、`run_d.sh`、`proxy.sh`，外部管理日志与后台进程。仅按已记录 PID/启动时间/进程组管理自有服务；不用全局 pkill。
4. AISBench 使用顶层 `bench_fm16.py`，P case=`p-only`、C2/8，PD case=`pd-zero`、C4/16，输出默认1/1024。每次使用新 `--seed-base`、独立 `--output-dir`。服务重启后重新预热；热服务只在已有预热可复用时用 `--skip-warmup`。
5. `TMPDIR` 与测试输出使用短路径容器共享内存，如 `/dev/shm/q38-bench-tmp`、`/dev/shm/q38-<唯一批次>`；完整校验后复制到 NFS。长 TMPDIR 曾导致 AF_UNIX path too long / SyncManager EOFError，NFS直接测量输出曾在 console关闭时出现 Remote I/O error；这两次客户端异常已排除成绩，仅补跑缺失轮次。
6. `.67` 根分区曾无可用空间，不删除其他项目材料。HTTP200/FIN递增不代替逐请求真实 token 和完整批次校验。
7. 两侧入口已配置 torch profiler、with_stack=false，输出为本项目 `profiling/p`、`profiling/d`；采集须显式 start_profile/stop_profile，诊断轮不混入正式评分。

## 证据与接续索引

远端相对于工作目录：

- 当前16K顶层脚本、README、`env.sh`、`pool_env.sh`、`mmc-local.conf`、`mmc-meta.conf`、两个代理 Python 文件、`bench_fm16.py`、`check_runtime.sh` 及 `.runtime/`。
- 首次三轮交付与所有权：`.work/loop-20261119/`，汇总 `results.json`、`results.csv`；原稿 `archive-20261119-0245/`。
- 停止现场：`.work/opt-20261119/`，`checkpoint.json`、`state.json`、`candidate-state.json`、`stop-controllers.json`、`baseline-drift/`、`p32k/`；优化前原稿 `archive-opt-20261119-012046/`（容器日志/时间戳采用UTC）。旧checkpoint关于后续调优的建议已被本文件“用户最新意图”覆盖。

本地工作区相对路径：

- `qwen38-pd-pp-mtp3-validation-20261119/`：已通过脚本、三轮全部原始指标、CSV和图。
- `qwen38-pd-pp-opt-preserved-20261119/`：停止现场审阅副本，含 `STOP-STATUS.md` 和隐藏过程记录。
- `.work/opt-20261119/preserved-scene.tar.gz`：原始停止归档，SHA256 `bae859e12ecc6d9d6ac20d0d0beb252c72b3dd21504d9ccc15b88009e6ee26df`。

共享知识库为 `tomleeai/codex-skills` 的 `knowledge-base/vllm-performance`。相关记录：

- `case-qwen38-pd-pp-mtp3-offload-layerwise-128k-20261119`：首次72条正式请求与原始完整指标。
- `case-qwen38-mtp3-pool-regression-loop-interrupted-20261119`：额外24条有效请求、32K被中断及用户停止记录。

共享版不含现场地址、私有绝对目录或凭据。已推送分支不等于已合入main或其他机器已拉取；使用时核对分支、修订与证据文件。
