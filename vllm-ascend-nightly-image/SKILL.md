---
name: vllm-ascend-nightly-image
description: 按日期查找 vLLM-Ascend Nightly Image Build (with base) 发布的日构建镜像，默认选择 main 分支的 A5 openEuler 多架构镜像。用于查询镜像名、转换蓝区与绿区镜像地址，或按用户指定的日期、区域和服务器执行 docker pull 并验证结果。
---

# 获取 vLLM-Ascend 日构建镜像

用户给出日期、蓝区或绿区、目标服务器并要求拉镜像时，直接查找并在该服务器执行。只问镜像名时，查出并返回地址即可。默认 `main`、`A5 openEuler`，用户指定的分支、芯片或系统优先。将“今天”等相对日期按 Asia/Shanghai 换成具体日期，再匹配镜像 tag 中的 `YYYYMMDD`。同一天有多个构建时，选最新成功发布且符合条件的版本。找不到时给出实际查到的候选及原因，换日期、分支或镜像类型由用户决定。

## 从 GitHub Actions 确认镜像

1. 打开 [Nightly Image Build (with base)](https://github.com/vllm-project/vllm-ascend/actions/workflows/schedule_nightly_image_build_with_base.yaml)，查看请求日期附近的 run，按 run 编号从新到旧核对。可用 GitHub CLI/API 列出 run 及其 jobs，或使用浏览器已有的 GitHub 会话。时间筛选只是缩小范围，按最终 tag 确认日期和分支。工作流用 UTC 生成 tag 日期，排队或重跑也可能让页面日期与 tag 日期不同。
2. 在 `Build nightly-a5 (openeuler)` 下打开成功的 `Merge multi-arch nightly-a5`，展开 `Create multi-arch manifest`，复制推送成功行中带构建日期和 `-rNNN` 的完整镜像名。确认它是最终的 nightly 多架构镜像。滚动 tag、base 镜像、临时仓库和带 `-amd64` 或 `-arm64` 的临时 tag 都应排除。若有多次 attempt，采用实际发布了所选 tag 的 attempt。run 的 `head_branch` 可供筛选，发布分支以 tag 为准。
3. 有 GitHub CLI 登录时可执行 `gh run view RUN_ID --repo vllm-project/vllm-ascend --job JOB_ID --log` 读日志。若日志不可用，可从该 run 的 `build-info-run-*` artifact 查发布记录，或用下方 SWR 查询核对实际存在的 tag。GitHub 匿名 API 可读取 run 和 job 元数据，例如 `GET /repos/vllm-project/vllm-ascend/actions/workflows/schedule_nightly_image_build_with_base.yaml/runs` 与 `GET /repos/vllm-project/vllm-ascend/actions/runs/RUN_ID/jobs`，日志和 artifact 下载可能需要登录。

### SWR 查询 A5 openEuler

使用随 skill 附带的 [SWR 查询脚本](scripts/find_a5_openeuler_tags.py) 按日期查到蓝区已发布的完整镜像名。例如在 skill 目录执行 `python scripts/find_a5_openeuler_tags.py 2026-11-17`。默认查 `main`，可传 `--branch releases/v0.30.0`，也可传 `--run-number 112` 只核对指定 run。结果按 run 编号降序排列。结合 Actions 确认所选镜像对应的 run 和多架构发布状态。若仓库路径变化或要查其他芯片/系统，使用对应发布日志中的地址。

## 映射仓库并在目标服务器拉取

- **蓝区**用发布记录中的完整地址。标准 A5 openEuler 镜像当前发布到 `swr.cn-north-12.myhuaweicloud.com/atlas_inference/vllm-ascend`，部分其他变体的发布日志使用 `swr.cn-north-12.myhuaweicloud.com/base_image/ascend-ci/vllm-ascend`。
- **绿区**使用用户给出的仓库地址，或已配置的 `VLLM_ASCEND_GREEN_REPOSITORY`（完整仓库地址，省略 tag）。将蓝区地址的整个仓库部分替换成该地址，原样保留冒号后的完整 tag，包括 CANN 构建号、日期和 run 编号。缺少仓库地址时先取得配置。绿区可用性以目标服务器实际拉取为准。

使用用户点名的服务器和已配置的 SSH 入口，确认目标及 Docker daemon，沿用已有的 Docker 权限和仓库凭据。在服务器上执行 `docker pull '完整镜像名'`，等到命令结束，再用 `docker image inspect '完整镜像名' --format '{{.Id}} {{json .RepoDigests}}'` 确认镜像已存在于该 daemon。连接中断后先检查同一目标上的镜像与拉取状态，必要时只重试同一个镜像。绿区返回 `manifest unknown` 时，报告请求的 tag 和错误，保留用户指定的区域与版本。

回复完整镜像名、目标服务器、拉取结果、可用的 digest 和 Actions job 链接。拉取失败时给出实际出错点。

## 例子

[2026-11-17 的 run #112](https://github.com/vllm-project/vllm-ascend/actions/runs/43995065074/job/133768848768) 对应以下地址，供核对映射。每次请求都重新查当次日期的发布记录。

```text
蓝区 swr.cn-north-12.myhuaweicloud.com/atlas_inference/vllm-ascend:nightly-ci-main-a5-openeuler-cann9.3.0_20261113161300-20261117-r112
绿区 <VLLM_ASCEND_GREEN_REPOSITORY>:nightly-ci-main-a5-openeuler-cann9.3.0_20261113161300-20261117-r112
```
