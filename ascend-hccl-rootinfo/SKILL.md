---
name: ascend-hccl-rootinfo
description: 排查 Ascend 950DT 单机 vLLM 的 HCCL 初始化失败，在目标容器内从实时 DCMI/EID 生成并校验 hccl_rootinfo.json，再验证进入权重加载；适用于缺文件或 local_id 越界，不能将所有 HCCL error 4 都归为此问题。
---

# Ascend 单机 HCCL rootinfo 修复

在用户授权的目标容器内定位、生成、验证 `/etc/hccl_rootinfo.json`。只把已经观察到的根因用于修复；`hcclCommInitRootInfoConfig ... error code 4` 是上层汇总错误，还需读取同一 worker 的底层 plog。

## 适用边界与经验

现场已验证的组合是 Ascend950DT、mainboard ID `0x27`（十进制 39）、单宿主 8 卡、驱动 `25.6.rc1`、CANN `9.2.0`、torch/torch_npu `2.10.0`、vLLM `0.30.0` / vLLM-Ascend `0.30.0rc2`。这是一项有条件的修复，其他硬件、拓扑或跨宿主组不能直接套用。

- 文件缺失、目录误挂载、旧机器 EID、错误拓扑和 `local_id` 越界是不同原因，先采集文件类型、mount 来源、内容及对应日志。
- 本案例 DCMI 返回 `Server Index=65535`、`Super Pod ID=4294967295`。非官方工具的默认 FM16 分支计算 `65535 * 8 + physical_id`，得到 `524280..524287`；HCCL 明确拒绝超出 `0..512` 的编号。
- 新容器没有文件时，CANN 自动探测也产生了同类越界 `local_id`。因此缺文件未必表现为 FileNotFound；应同时检查自动探测后的编号校验错误。
- 不修改硬件 Server Index、不对 JSON 的 ID 做无依据取模。使用工具包**已有的单机拓扑生成分支**：本机物理 ID `0..7`、`atlas_850_1.json`、第 0 层、die 1 的七个实际端口/EID。辅助脚本复用该分支，不修改已安装工具包。
- `net_instance_id` 中含 `65535` 的字符串并不等于数值 `local_id` 越界；本组合已在保留该分组标签的情况下成功初始化。
- 修复 HCCL 后仍可能 OOM。历史 4 卡 EP 配置在模型创建阶段 OOM；8 卡 EP 每 rank 分配 48/384 个专家，能够进入权重分片加载。不得把改成单实例 8 卡称为完成了 4+4 PD 分离。

## 现场步骤

1. 核实宿主、容器、镜像 digest、实时型号/物理 ID、软件版本、NFS 可读性、卡与端口占用。读取启动脚本；已有实例正在启动时先观察它，避免重复拉起。只能停止本轮明确归属的进程，不能按进程名批量清理或重置 NPU。
2. 记录容器内文件状态和宿主挂载来源：

   ```bash
   docker inspect --format '{{json .Mounts}}' "$CONTAINER"
   docker exec "$CONTAINER" ls -ld /etc/hccl_rootinfo.json
   ```

   如果它是目录，先确认目录内容和挂载关系，再备份处理；不要覆盖目录或误写 bind mount 后的宿主文件。缺文件不是所有版本的通用故障判据，以本次 plog 的消费路径为准。
3. 从服务日志取**本轮 worker PID**，在该容器中读取 `/root/ascend/log/debug/plog/plog-<PID>_*`；必要时读取 `run/plog`。只看到 error 4、最后的 EngineCore 退出或网络清理错误，不能确认首因。缺文件、打开/解析 rootinfo 失败、或明确的 `local_id ... out of range` 才进入本修复分支。
4. 若需复现，在用户授权的新容器中保留自然缺文件状态。固定镜像、启动脚本 SHA256、设备列表、并行参数与环境，先留存失败日志；不要删除在线容器的可用文件制造故障。
5. 将本技能的 `scripts/generate_rootinfo.py` 和经核对的工具发行包放进目标容器。优先使用版本匹配的官方生成器；本技能的适配脚本仅支持已审阅的 `unofficial-ascend-tools==0.0.11`，这是**非官方包**，不能称为官方安装。隔离安装与来源见 [工具来源和适配](references/tool-and-case.md)。
6. **在目标容器内执行**生成器，实时读取该容器可见设备的 DCMI/EID。以下命令也在容器内执行，Python 路径可按实际隔离环境替换：

   ```bash
   /opt/hccl-rootinfo-tools/bin/python /opt/hccl-rootinfo-skill/scripts/generate_rootinfo.py generate \
     --topology /usr/local/Ascend/driver/topo/950/atlas_850_1.json \
     --expected-ids 0 1 2 3 4 5 6 7 \
     --output /etc/hccl_rootinfo.json \
     --report /tmp/rootinfo-generation.json
   ```

   它会检查实际 mainboard/设备映射、拓扑的单机 P2P 端口、编号、层级与 EID 来源，再原子写入文件。已有普通文件需显式 `--replace`，会先备份；目录、符号链接、bind mount 输出路径将拒绝写入。不要从其他机器复制 EID，也不要仅凭 JSON 可解析就宣布通信通过。
7. 确认失败实例及其 workers 已退出，再用**完全相同的服务脚本**重启一次。独立保存前后日志，记录新 PID 和脚本 SHA256。若仍失败，按最新的底层首因继续；不要无限重试、静默降低模型参数或覆盖其他服务。

## 验收与停止点

- 所有计划 rank 的同一 communicator 都出现 `HcclCommInitRootInfoConfigV2 success`；保留 rank/identifier/PID，不能只取 rank 0。
- 服务进入实际权重读取，例如 EP weight filter 后出现 `Multi-thread loading shards`。单独的 `Loading model from scratch` 还可能处于模型创建阶段，不能作为已开始读取分片的唯一证据。
- 用户只要求验证到权重加载时，到此结束等待；报告“进入权重加载”，不要称为 ready、推理正确、PD/KV 或 MC2 验收通过。明确进程是继续运行还是已按用户指示停止。
- rootinfo 已通过但发生 allocator OOM、NFS D 状态、端口冲突或模型异常时，保留根因并转入对应排障；它们不能被当成 rootinfo 修复失败或通信成功的替代证据。

记录本次前后对照的版本、脚本摘要、缺文件/失败证据、生成器输出、全部 rank 成功标记和权重读取标记。现场 IP、密码、原始 EID 和业务脚本不要写入可分享的 skill。
