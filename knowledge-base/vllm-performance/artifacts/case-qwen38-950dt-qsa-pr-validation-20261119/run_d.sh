#!/bin/bash
set -euo pipefail

# PD role; set the node variables shown in the reproduction instructions.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/env.sh"

MODEL_PATH=${MODEL_PATH:?Set MODEL_PATH to the same W4A8-MXFP checkpoint}
MODEL_NAME=qwen3.8-flash-next
local_ip=${D_HOST:?Set the role endpoint}
nic_name=${NIC_NAME:?Set NIC_NAME for this node}

if [[ ! -s "$MODEL_PATH/config.json" ]]; then
  printf '缺少模型配置 %s/config.json\n' "$MODEL_PATH" >&2
  exit 1
fi

unset VLLM_USE_MODELSCOPE VLLM_PP_LAYER_PARTITION
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export ASCEND_LOCAL_COMM_RES='{"version":"1.3"}'
export VLLM_HOST_IP="$local_ip"
export HCCL_IF_IP="$local_ip"
export HCCL_SOCKET_IFNAME="$nic_name"
export GLOO_SOCKET_IFNAME="$nic_name"
export TP_SOCKET_IFNAME="$nic_name"
export HCCL_NPU_SOCKET_PORT_RANGE=61500-61599
export HCCL_OP_EXPANSION_MODE=AIV
export HCCL_ALGO=level0:fullmesh
export HCCL_BUFFSIZE=1024
export PYTHONHASHSEED=0
export VLLM_USE_V2_MODEL_RUNNER=1
export VLLM_USE_FASTOKENS=1
export VLLM_USE_RUST_FRONTEND=0
export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=3000
export VLLM_ENGINE_READY_TIMEOUT_S=4800

exec vllm serve "$MODEL_PATH" \
  --host "$local_ip" \
  --port 8248 \
  --served-model-name "$MODEL_NAME" \
  --tensor-parallel-size 4 \
  --data-parallel-size 2 \
  --data-parallel-address "$local_ip" \
  --data-parallel-rpc-port 29780 \
  --enable-expert-parallel \
  --max-model-len 143360 \
  --max-num-seqs 16 \
  --max-num-batched-tokens 4096 \
  --gpu-memory-utilization 0.9 \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --mamba-cache-mode align \
  --mamba-ssm-cache-dtype bfloat16 \
  --async-scheduling \
  --seed 1024 \
  --safetensors-load-strategy eager \
  --language-model-only \
  --trust-remote-code \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 \
  --engram-config '{"cpu_offload":true,"dp_shared_memory":true}' \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --additional-config '{"enable_flashcomm1":true,"enable_cpu_binding":true,"enable_shared_expert_dp":true,"multistream_overlap_shared_expert":false,"enable_fused_mc2":0,"scheduler_config":{"recompute_scheduler_enable":true},"ascend_compilation_config":{"fuse_norm_quant":false,"enable_npugraph_ex":true,"enable_static_kernel":false,"enable_super_kernel":false}}' \
  --speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":3,"enforce_eager":false}' \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV2","kv_role":"kv_consumer","kv_port":"32480",
    "kv_connector_extra_config":{"prefill":{"dp_size":2,"tp_size":4},"decode":{"dp_size":2,"tp_size":4}}}' \
  --profiler-config '{"profiler": "torch", "torch_profiler_dir": "./profiling/d", "torch_profiler_with_stack": false}'
