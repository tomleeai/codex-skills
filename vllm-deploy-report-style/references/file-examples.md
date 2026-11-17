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

在命令里逐行展开多层 JSON，保留一个 shell 参数。以下 connector 仅作缩进示例。

```bash
  --kv-transfer-config '{
    "kv_connector": "MooncakeHybridConnector",
    "kv_role": "kv_producer",
    "kv_connector_extra_config": {
      "prefill": {
        "dp_size": 2,
        "tp_size": 4
      },
      "decode": {
        "dp_size": 4,
        "tp_size": 2
      }
    }
  }'
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

下面展示简短的组织方式；实际状态与命令按本轮工作填写，不照抄“未启动”。占位项应填为现场值，未核实则标明未知。loop 时先将原稿移入工作目录内新建的归档子目录，再在顶层维护当前脚本和 README。

````markdown
# 模型名 PD 服务

工作目录 `/path/to/work`，在两台机器现有的 `model-pd` 容器内执行。模型名 `my-model`，权重 `/path/to/model`，权重版本/格式按本次实值填写。目标 case 与最小化、全特性或专项测试范围按本轮要求简短说明。

## 布局与参数

| 角色 | 主机 / 容器 | 卡 | 并行布局 | HTTP | 脚本 |
|---|---|---|---|---|---|
| P | 192.0.2.10 / model-pd | 0～7 | DP2 / TP4 / EP8 | 7100 | `run_p.sh` |
| D | 192.0.2.20 / model-pd | 0～7 | DP4 / TP2 / EP8 | 7100 | `run_d.sh` |
| Proxy | 192.0.2.10 / model-pd | — | 连接 P、D 两个 API | 8000 | `proxy.sh` |

按脚本实值补一张 P/D 参数表，列上下文、batch token、序列上限、显存利用率和实际存在的 MTP / 图模式差异。公共特性用短句概括；正文仅解释必要的限制，不复述表格数字或展开并行原理。

## 环境与改动

用一两句填写实际镜像、硬件、驱动、CANN、PyTorch、vLLM/vLLM-Ascend 版本；多机注明实际镜像是否已核对一致，未核实项集中标明。再简述相对参考脚本的必要改动、原因和影响；无改动则写无改动，影响启动的未解决项需保留。

## 手工拉起

现有容器和权重已准备好，运行环境由脚本初始化。以下为重新启动步骤，先核对当前实例状态。两台宿主机各开终端，执行共同的进入步骤：

```bash
cd /path/to/work
docker exec -it -w "$PWD" model-pd bash
mkdir -p logs
```

P，192.0.2.10 的容器内：

```bash
nohup bash run_p.sh > logs/p.log 2>&1 &
```

D，192.0.2.20 的容器内：

```bash
nohup bash run_d.sh > logs/d.log 2>&1 &
```

两侧全部 rank 就绪后，在 P 容器内检查并启动 Proxy；任一检查失败时先处理，不继续启动：

```bash
if curl --noproxy '*' -fsS --max-time 10 http://192.0.2.10:7100/health &&
   curl --noproxy '*' -fsS --max-time 10 http://192.0.2.20:7100/health; then
  nohup bash proxy.sh > logs/proxy.log 2>&1 &
fi
```

## 手工验证

在 P 容器内执行：

```bash
curl --noproxy '*' -fsS --max-time 10 http://192.0.2.10:7100/health
curl --noproxy '*' -fsS --max-time 10 http://192.0.2.20:7100/health
curl --noproxy '*' -fsS --max-time 10 http://192.0.2.10:8000/healthcheck
curl --noproxy '*' -fsS --max-time 600 http://192.0.2.10:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"my-model","messages":[{"role":"user","content":"你好"}],"max_tokens":16}'
```

通过标准：健康检查均返回 HTTP 200；请求的 `choices` 包含有效输出，PD 日志无 KV 传输致命错误，请求后服务仍健康。以上接口须按实际代理和模型能力调整；多入口场景应逐个检查实际 endpoint。

本轮仅完成配置校验，尚未执行健康和模型请求验证。按实际结果更新此处，不将建议命令写成已通过。

原稿归档：`/path/to/work/archive-YYYYMMDD-HHMMSS/`；归档内容未改动。当前脚本直接位于工作目录顶层，以上命令使用这套脚本。过程记录：`/path/to/work/.work/本次运行/`。

## Profiling 采集与解析

按下方示例填写本部署的启动参数、控制端口、采集负载和结果路径。
````

如果模型请求已经验证，将末段更新为简短的实际结果；可在首段注明当前状态。如果仍在加载，不应写“服务正常”。README 里的命令和参数只对应当前版本。

多机同组场景，将启动步骤按“角色 + 机器 + 容器”继续展开，并注明哪些 rank 需及时一起加入；不要等组内首节点健康才启动其余 rank。参数表负责对照，环境与改动用短段收束，源码分析和逐轮记录留在隐藏过程目录。

## Profiling 采集与解析

下面以本页示例的 P/D 两侧为例。在对应容器中将工作目录切到 `/path/to/work`。给 P 的 `vllm serve` 命令末行加续行符 `\`，再追加以下参数。D 同样配置并改用 `./profiling/d`。单实例写 `./profiling`。工作目录应可写，容器内的目录要能在采集后访问。

```bash
  --profiler-config '{
    "profiler": "torch",
    "torch_profiler_dir": "./profiling/p",
    "torch_profiler_with_stack": false
  }'
```

P/D 启动并完成预热后，按实际节点与端口开启采集。两侧开始后，通过 Proxy 发送上文的验证请求或本轮指定负载。

```bash
curl --noproxy '*' -fsS -X POST http://192.0.2.10:7100/start_profile
curl --noproxy '*' -fsS -X POST http://192.0.2.20:7100/start_profile
```

目标请求完成后停止采集，等两侧输出写完。若中途失败，也结束已经开启的采集。接口返回 404 时先核对目标是否是实际 vLLM 端口，以及进程启动时是否加载了 `--profiler-config`。

```bash
curl --noproxy '*' -fsS -X POST http://192.0.2.10:7100/stop_profile
curl --noproxy '*' -fsS -X POST http://192.0.2.20:7100/stop_profile
```

回到采集实例的工作目录，查询本轮生成的目录。多 rank 时按需要解析对应的目录。

```bash
find ./profiling -type d -name '*_ascend_pt'
```

在有匹配 torch-npu 的环境执行下列 Python 代码，把示例路径换成上一步输出的完整目录名。

```python
from torch_npu.profiler.profiler import analyse

analyse("./profiling/p/实际目录名_ascend_pt")
```

解析结果位于该目录下的 `ASCEND_PROFILER_OUTPUT/`。可用 MindStudio Insight 打开 `trace_view.json` 看时间线，或查看 `kernel_details.csv` 核对 kernel 耗时。[vLLM-Ascend 服务性能分析指南](https://docs.vllm.ai/projects/ascend/zh-cn/latest/developer_guide/performance_and_debug/service_profiling_guide.html)

## 辅助文件

常规交付可只有以下文件，实际任务按需取舍：

```text
work/
  run_p.sh
  run_d.sh
  proxy.sh
  load_balance_proxy_server_example.py
  README.md
  archive-YYYYMMDD-HHMMSS/
    run_p.sh
    run_d.sh
    proxy.sh
    load_balance_proxy_server_example.py
    README.md
  .work/
    本次运行/
```

loop 时先将原稿移入 `archive-YYYYMMDD-HHMMSS/` 并保持内容不变，顶层工作副本用于修复与最终验证；成功后保留这一套可直接执行的当前脚本。逐轮日志、脚本快照和工具状态集中在 `.work/本次运行/`，README 说明归档、过程记录及实际状态。不要把控制过程写进服务脚本，也不要为了展示此目录结构创建空文件。
