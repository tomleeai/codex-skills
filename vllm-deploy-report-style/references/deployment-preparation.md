# 部署准备与容器模板

用于整理部署输入、选择参考基线，以及本次确需创建 Docker 容器时生成操作说明。本文件不授予服务器连接、容器创建、服务启停或 benchmark 权限。

## 按场景补齐输入

从本轮及已有会话提取信息，缺什么补什么；不要求用户重复提供，不为填表查询无关环境。

| 信息 | 需要明确的内容 |
|---|---|
| PD 形态 | 混部或分离，P/D 角色如何对应机器与卡 |
| 硬件形态 | 单机单卡、单机多卡或多机多卡；型号、卡型、可用卡及相关互联要求 |
| 实际服务器 | 已分配的主机到角色的映射；需要上机时提供 SSH 账号、地址、必要端口和认证方式 |
| 工作与权重路径 | 工作目录、权重目录及格式；多机共享路径在宿主机和容器中的对应关系 |
| 场景与 case | 模型、上下文上限、输入/输出长度、并发或请求速率，以及用户指定的时延/吞吐目标；未指定的指标不自行编造 |
| 特性范围 | 最小化拉起、常规全特性测试或池化等专项；哪些特性及参数需要保留 |
| Docker | 指定镜像的 repository、tag、image ID；现有容器名或需要新建的容器名 |
| 参考与交付 | 同事成功脚本或官方示例的位置，本次文件目录，以及只编写、人工拉起还是已授权 agent 操作 |

SSH 密码只通过连接交互使用，不写入上述表格、脚本、README、命令行或共享技能。已有密钥、agent 或有效会话时不额外索要密码。未授权上机时，用用户提供的信息编写并注明未核实项。

部署 README 的开头把场景压成短段即可，例如“模型及格式，PD 形态与硬件范围，面向某类长度/时延 case，本次保留哪些特性”。随后用布局和参数表展开，不在前言重复整张表。

## 镜像与参考基线

- 用户可贴 `docker images` 输出作为镜像选择依据，保留 repository、tag、image ID 等身份信息；示例中的创建时间和体积只是现场信息，不作为版本标识。
- 多机应使用一致镜像。需要且已授权核实时，结合 `docker image inspect IMAGE_ID` 和现有容器的 `docker inspect CONTAINER_NAME` 比较实际 image ID / digest；只看名称或可变 tag 不足以确认一致。发现不一致先说明，按用户选择处理，不默认为一致，也不擅自替换已有容器。
- 优先读取用户提供的同事成功脚本，核对其模型、硬件、镜像、并行布局、特性和 case 与目标现场的差异。没有合适现场基线时，从 [vLLM-Ascend 官方部署入口](https://docs.vllm.ai/projects/vllm-ascend-cn/zh-cn/latest/getting_started/overview.html) 查对应模型、PD 形态及版本示例；`latest` 示例仍需对照实际镜像。
- 将参考适配成现场脚本，明确替换的地址、网卡、路径、角色与端口。若版本不支持某参数，列出证据、变更及影响，按本轮用户约定处理；不将删除参数表述为特性仍然保留。

## Docker 创建模板

优先保留用户熟悉的 `docker.sh` 和双参数调用方式：

```bash
bash docker.sh IMAGE_ID CONTAINER_NAME
```

下面沿用用户提供的八卡 Ascend 模板：先检查参数数目，再读取 `$1`、`$2`；镜像与容器名加引号，移除重复的 npu-smi 挂载。模板的八张卡、500g shm、host 网络/PID、privileged 和挂载列表属于该环境的配置，按本次实际硬件与基线调整；单卡或部分卡部署只映射本次使用的设备。

```bash
#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo '用法：bash docker.sh IMAGE_ID CONTAINER_NAME' >&2
    exit 1
fi
IMAGES_ID=$1
NAME=$2

exec docker run -u root -it -d \
    --name "$NAME" \
    --net=host \
    --pid=host \
    --shm-size=500g \
    --privileged=true \
    --device=/dev/davinci_manager \
    --device=/dev/hisi_hdc \
    --device=/dev/ummu \
    --device=/dev/uburma \
    --device=/dev/davinci0 \
    --device=/dev/davinci1 \
    --device=/dev/davinci2 \
    --device=/dev/davinci3 \
    --device=/dev/davinci4 \
    --device=/dev/davinci5 \
    --device=/dev/davinci6 \
    --device=/dev/davinci7 \
    -v /root/host:/root/host \
    -v /usr/local/sbin/npu-smi:/usr/local/sbin/npu-smi \
    -v /usr/local/sbin:/usr/local/sbin \
    -v /usr/local/dcmi:/usr/local/dcmi \
    -v /var/log/npu:/usr/slog \
    -v /mnt:/mnt \
    -v /data:/data \
    -v /usr/lib64:/usr/lib64 \
    -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
    -v /home:/home \
    -v /etc/hixlep:/etc/hixlep \
    -v /lib/route.conf:/lib/route.conf:ro \
    -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
    -w /home \
    "$IMAGES_ID" \
    bash
```

编写现场版本时，核对设备和宿主路径确实存在；rootinfo、公共拓扑、路由等额外挂载按目标平台和已有成功容器补齐，不将这份设备列表当作所有 Ascend 型号的通用配方。每台宿主机分别执行创建命令，传入已确认一致的镜像；名称按现场约定。已有容器可复用时，README 直接从进入容器和工作目录开始，不引导用户重复创建或删除现有容器。

## 从脚本交付到 benchmark

1. **交付脚本**：结合场景和参考脚本生成当前机器、容器可用的入口与 README；标明配置校验和未验证项。只编写时不执行服务或压测。
2. **人工拉起与验证**：用户按角色和机器对应的命令启动，检查所有实际入口及最小模型请求。成功后进行 benchmark，记录基准。
3. **失败后进入 loop**：先保留启动日志、实际命令/参数、对应节点/容器和失败阶段，再在用户授权下定位与重试。按主 skill 的规则，将原稿移入工作目录内新建的归档子目录，再修复顶层工作副本；成功后顶层直接呈现验证后的脚本和 README，原稿与过程记录各自集中保存。
4. **人工复验与 benchmark**：复验修复后的服务，再按确定的 case 测基准；记录对应脚本、镜像、权重、并行/特性、负载参数和实际结果，避免把不同配置的数字混为同一基准。

以上是默认协作顺序。若本轮已授权 agent 直接启动、loop 或 benchmark，按该授权推进，不强制等待人工重复操作；具体排障与性能方法使用相应运行流程，本 skill 只规定交付如何写清楚。服务启动、请求成功和性能基准是不同结果，不以其中一项代替其他项。
