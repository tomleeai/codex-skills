# 文件排版示例

以下只展示文件形状，不是已验证的部署配方。地址使用文档示例网段；模型、并行度、connector、环境变量和全部特性参数都应来自当前用户文件与目标版本。需要保留的配置应直接补到命令中，不能因示例省略就删掉。

## run_p.sh / run_d.sh

每份脚本都独立展开；不拆出公共配置层。以下为 P 侧示意，D 侧同样直接写清自己的卡、IP、DP/TP和参数。

```bash
#!/bin/bash

set -euo pipefail

# P 节点，在目标容器内执行：bash run_p.sh
MODEL_PATH=/path/to/model
MODEL_NAME=my-model
nic_name=eth0
local_ip=192.0.2.10

export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export VLLM_HOST_IP="$local_ip"
export HCCL_IF_IP="$local_ip"
export GLOO_SOCKET_IFNAME="$nic_name"
export HCCL_SOCKET_IFNAME="$nic_name"

exec vllm serve "$MODEL_PATH" \
  --served-model-name "$MODEL_NAME" \
  --host 0.0.0.0 \
  --port 7100 \
  --data-parallel-size 2 \
  --data-parallel-size-local 2 \
  --api-server-count 1 \
  --data-parallel-address "$local_ip" \
  --data-parallel-rpc-port 12321 \
  --tensor-parallel-size 4 \
  --enable-expert-parallel
```

这只展示原生内部 DP 的排版，缺少具体模型和 PD 特性参数。不要将它当作完整 P 服务启动命令。若当前版本/模型不支持这种 DP 组织，应保留其实际启动方式。

复杂 JSON 仍可直接阅读，例如目标版本确实支持下列 connector 时：

```bash
  --kv-transfer-config '{"kv_connector":"MooncakeHybridConnector","kv_role":"kv_producer","kv_connector_extra_config":{"prefill":{"dp_size":2,"tp_size":4},"decode":{"dp_size":4,"tp_size":2}}}'
```

同一部署的 D 文件写 `kv_consumer`，其中的 `prefill` 和 `decode` 布局仍与 P 文件相同。这里展示的数值不是容量或性能推荐。

## proxy.sh

```bash
#!/bin/bash

set -euo pipefail
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 在主节点执行；先确认两侧全部 DP rank 就绪。
exec python3 "$script_dir/load_balance_proxy_server_example.py" \
  --prefiller-hosts 192.0.2.10 \
  --prefiller-ports 7100 \
  --decoder-hosts 192.0.2.20 \
  --decoder-ports 7100 \
  --host 0.0.0.0 \
  --port 8000
```

参数名称依目标代理源码核实。这里每侧各一个入口，是因为这个例子用了每侧一个 API server 的内部 DP；不是所有 PD 部署都应这样填写。

## README.md

下面展示简短的组织方式；实际状态与命令按本轮工作填写，不照抄“未启动”。占位项应填为现场值，未核实则标明未知。loop 时文件位于独立交付目录，不覆盖原 README。

````markdown
# 模型名 PD 服务

在现有容器内进入 `/path/to/work/deploy` 执行。当前只完成配置校验，尚未启动模型。

## 环境与模型

- 主机/容器：填写实际主机与容器标识。
- 硬件、驱动、CANN、PyTorch、vLLM/vLLM-Ascend：填写实际型号与版本，未核实项标明未知。
- 模型名：`my-model`；权重：`/path/to/model`；权重版本/格式：填写实测信息或标明未知。

## 配置

| 角色 | 业务 IP | 设备 | DP | TP | EP | HTTP |
|---|---|---|---:|---:|---:|---|
| P | 192.0.2.10 | 0..7 | 2 | 4 | 8 | 7100 |
| D | 192.0.2.20 | 0..7 | 4 | 2 | 8 | 7100 |

DP 为数据并行，TP 为张量并行；EP 列填写当前工具定义的专家并行规模。补充实际上下文上限、并发上限、显存利用率及一句话作用说明，与脚本保持一致。

## 问题、修复与差异

简述遇到的问题、采用的方法和结果；列出相比原脚本的实际变更及原因。无改动则明确写无改动，未解决项如实列出。原文件保持不变，以下启动命令使用交付副本。

## 人工启动

先写清容器进入方式、必要环境初始化和权重挂载等前置条件。P、D 各开终端，进入各自交付目录：

```bash
# P 容器
bash run_p.sh

# D 容器
bash run_d.sh
```

确认两侧全部 rank 就绪后，在主节点另开终端，进入交付目录：

```bash
curl --noproxy '*' -fsS http://192.0.2.10:7100/health
curl --noproxy '*' -fsS http://192.0.2.20:7100/health
bash proxy.sh
```

客户端入口：`http://192.0.2.10:8000/v1`，模型名 `my-model`。

## 人工验证与本轮结果

两侧健康检查应返回 HTTP 200。Proxy 启动后在客户端终端执行：

```bash
curl --noproxy '*' -fsS http://192.0.2.10:8000/health
curl --noproxy '*' -fsS http://192.0.2.10:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"my-model","messages":[{"role":"user","content":"你好"}],"max_tokens":16}'
```

请求应成功返回，响应的 `choices` 中包含有效输出。以上接口须按实际代理和模型能力调整；无 `/health` 接口的代理应采用实际支持的检查方式。

本轮仅完成配置校验，尚未执行健康和模型请求验证。按实际结果更新此处，不将建议命令写成已通过。

过程记录：`/path/to/work/.work/本次运行/`。原文件仍位于原路径，未修改。
````

如果模型请求已经验证，应替换首段和末段的状态，给出简短结果；如果仍在加载，不应写“服务正常”。README 里的命令和参数只对应当前版本。

## 辅助文件

常规交付可只有以下文件，实际任务按需取舍：

```text
work/
  deploy/
    run_p.sh
    run_d.sh
    proxy.sh
    load_balance_proxy_server_example.py
    README.md
  .work/
    本次运行/
```

loop 时 `deploy/` 中的脚本和依赖为独立工作副本，原文件保留原处。逐轮日志、脚本快照和工具状态集中放在工作目录的隐藏子目录 `.work/本次运行/`，支持时设置隐藏属性；不要把控制过程写进 `run_p.sh`、`run_d.sh`，也不要为了展示此目录结构创建空文件。
