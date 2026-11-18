#!/bin/bash
# 由 P/D 或 Meta 入口加载。CANN/ATB 初始化脚本会读取未设置的变量。
qwen_init_runtime() {
  local nounset=0
  [[ $- == *u* ]] && nounset=1
  set +u
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
  source /usr/local/Ascend/cann-9.2.1/share/info/ascendnpu-ir/bin/set_env.sh
  source /usr/local/Ascend/nnal/atb/set_env.sh --cxx_abi="${ATB_CXX_ABI:-1}"
  if (( nounset )); then
    set -u
  fi
}
qwen_init_runtime
unset -f qwen_init_runtime

unset ftp_proxy FTP_PROXY http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY
