#!/bin/bash
set -euo pipefail

# PD role; set the node variables shown in the reproduction instructions.
unset ftp_proxy FTP_PROXY http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

exec python /vllm-workspace/vllm-ascend/examples/disaggregated_prefill_v1/load_balance_proxy_server_example.py \
  --host ${P_HOST:?Set P_HOST} \
  --port 8246 \
  --prefiller-hosts ${P_HOST:?Set P_HOST} \
  --prefiller-ports 8247 \
  --decoder-hosts ${D_HOST:?Set D_HOST} \
  --decoder-ports 8248 \
  --workers 1
