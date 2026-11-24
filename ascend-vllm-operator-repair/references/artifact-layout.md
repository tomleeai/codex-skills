# 现场目录与中文报告

借鉴已使用过的“入口脚本 / 环境 / 依赖 / 日志 / 修复记录”分离布局。一个独立 run 对应一次有边界的修复任务；每轮测试独立编号，不使用不断覆盖的 `test.log` 或无法区分版本的 `final2`。时间默认写明时区，设备日志 UTC 原文保留。

```text
<用户指定工作目录>/operator-repair/<YYYYMMDD-HHMMSS>-<case-id>/
├── manifest.json                 范围、原件/修复路径、哈希、版本和有效配置
├── originals/                    原 sh、必要 env/source、修改前文件快照
├── launch/                       与原 sh 同风格的带注释修复副本
├── overlays/python/ops/          Python 包修复副本；其他类型用 overlays/native 等
├── patches/                      命名为 <组件>-<用途>.patch 的 unified diff
├── probes/                       独立算子复现、参考计算与使用说明
├── logs/
│   ├── 00-baseline/              原配置日志、设备首因、客户端原始输出
│   ├── 01-<候选名>/             本轮独立 service/benchmark/device 日志
│   └── 02-<候选名>/
├── results/                      逐轮 JSON、逐请求明细、metrics、最终 VALIDATION.json
└── reports/REPORT.md             中文结论、证据索引、部署和回退
```

按需建目录，用户已有 `logs/<负载>-<日期>/` 时可直接沿用；保留原路径，不为了套模板迁移正在使用的文件。依赖源码、构建产物、安装目标、运行缓存应分清，特别记录 NFS 构建与本地输出的差异。不要复制权重、大型缓存或整份业务数据进入技能或报告。

## manifest 的实用字段

- `case_id`、run ID、开始/结束时间与时区；目标容器/工作目录与只读目录（仅存现场文件）。
- 原服务入口和关键 source 的路径、SHA256；原 sh 快照和修复 sh 的路径、diff、注释块说明。
- 平台、CANN/驱动、torch/torch-npu、vLLM/vLLM-Ascend、算子包版本、源码提交；未知就注明未核实。
- 实际 argv/env、并行、特性、最大长度及 batch/显存预算；哪些值由 source/默认值决定。
- 源码、原生库、安装目录与有效加载路径；原包是否已被其他操作修改，重装是否会覆盖补丁。
- 每轮假设、绑定的补丁/包哈希、日志路径、所有权/PID/starttime、试验状态和退出码。
- 测试原始命令、数据种子、输入/输出、请求数、并发、缓存指标、客户端重试语义。

不要把 helper 的 manifest 当作完整运行事实；准备阶段没有采集的字段，在现场核实时追加。

## REPORT.md 结构

1. **结论与适用范围**：已修复/定位/未完成，精确版本与负载，最终状态核实时间。
2. **原配置与不变项**：入口、source、原参数与修复副本 diff；新增加载语句为什么必要。
3. **首因与证据链**：最早 kernel/rank/错误、异步暴露点、独立复现；未证明的底层机制另列。
4. **逐轮记录**：原失败、无效客户端尝试、失败候选、成功候选，不覆盖或删除失败。
5. **最小修复**：具体文件/函数、补丁前后哈希、带注释的 sh、实际加载证据。
6. **数值与端到端验收**：各自判据和原始证据；请求/输出/错误/缓存/健康/同一实例，指标表附负载。
7. **使用、回退与重建**：已核实的入口、是否仍有实例运行、怎样恢复原副本、补丁持久化状态。
8. **限制**：未测形状/上下文/平台/版本、可能的性能代价，不把短压测写成长期稳定性认证。

## sh 注释块示例

放在原环境脚本之后，原 vLLM 命令原样保留：

```bash
source /work/env.sh || exit $?
# BEGIN OPERATOR-FIX [qli-qsli-ld]
# Load the local QLI/QSLI metadata repair; see patches/ and reports/REPORT.md.
# Rollback: stop this instance and use the original entry; remove this block only after revalidation.
export PYTHONPATH='/work/operator-repair/20261023-120000-qli-qsli-ld/overlays/python'${PYTHONPATH:+:$PYTHONPATH}
# END OPERATOR-FIX [qli-qsli-ld]
```

注释必须对应本次补丁，不照抄过期结论。若外层包装脚本不足以覆盖 source 后的环境，插入原 sh 的副本；不要把几十行服务命令手工重写成另一套参数。
