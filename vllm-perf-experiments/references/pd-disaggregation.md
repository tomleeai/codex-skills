# 单机/多机Prefill–Decode分离实验

本文件是后续实验设计方法。本skill来源案例只测过独立TP1/DP1单卡副本，**没有验证PD部署、KV connector或跨机传输**。PD用于延迟隔离与资源分配，不保证提高TPS。

## 先做兼容性预检

对照目标**安装版本/commit**核实模型、架构状态、KV布局/dtype、block size、MTP/投机、prefix caching、图编译、P/D并行组合与connector是否兼容。尤其混合注意力/状态空间模型可能不只是传标准attention KV；模型在合设服务可运行不意味着支持该PD路径。

把vLLM、vLLM-Ascend、CANN/驱动、PyTorch、通信库、connector插件和代理版本记录到各节点。latest/main文档仅用来发现能力，不能证明旧commit支持；对照tag、源码与该版本示例再生成具体命令。未确认前不提供“拿来即跑”的PD启动参数。

## 拓扑清单与资源对照

每个实例记录host/container、物理NPU、P或D角色、TP/DP/PP、CPU/NUMA、服务/metrics/RPC/KV端口、NIC/NPU接口、传输后端与代理。**1P2D首先表示实例配比，不等于3张物理卡**；同时报告各实例卡数与总卡数。

单机核对实际KV传输路径，不假定一定走卡间高速互联。多机另核对链路连通/带宽、接口选择、拥塞、MTU、通信库和安全设置；HTTP可达不代表KV数据面可达。设备平台和connector版本可能有不同通信要求，端口含动态范围时也要隔离。

主对照使用相同总卡数、硬件类别、精度/量化和服务目标：例如相同卡预算的合设副本集群与PD分离集群，入口代理与流量路由应可比较。不同卡数实验要标为扩展效率，补TPS/卡；不能拿2卡PD对1卡合设来归因分离本身。

## 分阶段确定问题

1. **功能与计数预检**：单请求验证P→传输→D全链路、输出/流式语义、cache状态、超时与请求ID关联；再用一批完整负载预检缓存压力和并发。
2. **资源配比**：固定每实例TP/DP及其余参数，在固定总预算内改变P:D。若整数配比改变总卡数，单独作为扩展实验。记录P、D排队与利用情况，不只看最终TPS。
3. **阶段参数**：固定拓扑与P:D，只改P或D的一个参数，明确flag归属。不要同时改变P预算、D序列数与并发后仍称单变量。
4. **负载曲线**：另设固定/分布长度、长短混合与到达率/并发扫描，报告TTFT/TPOT SLO下的有效吞吐（goodput，定义阈值和分母）。有限批次与稳态容量分开。
5. **交互验证**：需要联合推荐P/D配置时追加少量交叉点，避免把各阶段最优直接拼接。

负载扫描、拓扑扫描和启动参数扫描可以共同组成研究，但必须拥有可识别的协议层次，不能在一个单变量曲线上混合。

## 端到端与阶段指标

客户端经实际代理入口测TTFT、TPOT/ITL、输出TPS、E2E、P90/P99、失败与重试。再用请求ID关联：代理等待/路由、P队列/prefill、KV等待与传输字节/耗时、D队列/decode、回退重算。第一个HTTP/SSE空事件不必然是有效首生成token。

KV传输与计算可能重叠；不能直接相加各阶段均值代替端到端关键路径。跨机时间线需要同步时钟和明确误差；本地duration优先单调时钟。不能用墙钟差或缺少对应request ID的日志拼“精确阶段耗时”。

一个逻辑请求可能产生多次内部请求。客户成功数与内部P/D计数不要求简单相等：应依据已验证路由推导预期计数；最终输出TPS只计客户端实际输出一次。重试/失败/回退不能悄悄从延迟或成功率中消失。

固定代理版本、CPU资源、路由/负载均衡、连接池、流式转发、超时重试和prefix亲和性。普通与layerwise connector可能有不同数据流，换connector或代理等于改变被测系统条件。

## 缓存、干扰与恢复

分别定义P端prefix缓存、待发送KV持有、D端接收/复用和释放策略的初始状态。案例中的精确token前缀+guard方案不能直接当作PD起始状态已验证；要验证整条路径。观察两侧缓存、活跃/排队、抢占、传输失败和回退，运行命中仍可能是结果变量。

多个PD实验共享NIC/交换网络时，即使卡已隔离，也可能强烈干扰；先判断是否应串行。多机共享权重盘、跨NUMA和主机CPU也纳入资源预算。缓存持有超时不任意拉长来“修复”压测；先调查吞吐、失败回收与连接生命周期。

断点和所有权扩展到代理、P、D及各host：逐节点身份/锁、同协议有效批次、健康与错误状态。故障先收集对应请求的代理/P/D/传输片段，再定位；重复启动或只看代理health不足以确认服务链正常。恢复时只处理自有实例，逐节点验证用户指定原配置及额外资源释放。

## 官方入口（使用时核对安装版本）

- [vLLM Disaggregated Prefilling](https://docs.vllm.ai/en/latest/features/disagg_prefill/)：架构、connector与限制。
- [vLLM生产指标](https://docs.vllm.ai/en/latest/usage/metrics/)：阶段/队列指标；名称和后端需按版本确认，NIXL指标不能当作Mooncake指标。
- [vLLM-Ascend单机PD指南](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/features/pd_disaggregation_mooncake_single_node.html)。
- [vLLM-Ascend多机PD指南](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/features/pd_disaggregation_mooncake_multi_node.html)。
- [vLLM-Ascend PD设计](https://docs.vllm.ai/projects/ascend/en/main/developer_guide/Design_Documents/disaggregated_prefill.html)：不要把main当前支持项固化为所有版本能力。
