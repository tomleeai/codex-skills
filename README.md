# Codex 技能库：Ascend 与 vLLM

这个仓库收录了昇腾设备上的 vLLM 部署、排障、性能测试、上游修复和源码学习技能，以及 Codex 会话排障技能。每个技能的入口是 `SKILL.md`，里面说明了适用场景、操作步骤和如何验证结果。

## 选哪个技能？

| 你遇到的场景 | 对应技能 |
| --- | --- |
| Ascend 950DT 单机启动 vLLM 时，HCCL 初始化失败，日志提示 `hccl_rootinfo.json` 异常或 `local_id` 越界。 | [ascend-hccl-rootinfo](ascend-hccl-rootinfo/SKILL.md) 帮你确认原因，在目标容器中生成并校验配置，再检查服务是否进入权重加载。 |
| vLLM-Ascend 启动、预热或压测时出现设备算子异常。 | [ascend-vllm-operator-repair](ascend-vllm-operator-repair/SKILL.md) 从首个故障 kernel 入手，复现问题、做最小修补并验收原场景。 |
| 需要查某一天的 vLLM-Ascend 日构建镜像，或在指定服务器验证拉取结果。 | [vllm-ascend-nightly-image](vllm-ascend-nightly-image/SKILL.md) 核对 Actions 和实际发布的 tag，默认查询 A5 openEuler 多架构镜像。 |
| 需要写 vLLM 启动脚本、部署说明，或把排障过程整理给同事。 | [vllm-deploy-report-style](vllm-deploy-report-style/SKILL.md) 帮你交代在哪运行、怎么启动、改了什么，以及如何手工验证。 |
| 需要从需求走到性能验收、排查基线劣化，或用 AISBench 比较参数、卡数和预填充／解码分离方案。 | [vllm-perf-experiments](vllm-perf-experiments/SKILL.md) 帮你审核验收口径、设计可比实验，在每个输出 token 的耗时（TPOT）达标时选择吞吐更高的配置。 |
| 已有 vLLM-Ascend 现场补丁，需要准备上游 PR 或回复 review。 | [vllm-ascend-upstream-fix](vllm-ascend-upstream-fix/SKILL.md) 帮你整理补丁前的触发现场、分支选择、回归测试和补丁后的服务验证。 |
| 想从源码理解 vLLM-Ascend 的执行流程、组件关系，或续写学习笔记。 | [vllm-ascend-learning](vllm-ascend-learning/SKILL.md) 按实际版本走读源码，并把流程、关键位置和待验证项纳入笔记。 |
| Codex 回合出现 HTTP 400 `Bad Request`，或长任务中途失败。 | [codex-badrequest-triage](codex-badrequest-triage/SKILL.md) 从本地会话记录核对失败前后的事件，并区分证据与推断。 |

## 安装和使用

1. 在 Codex 中发一句「请把 https://github.com/tomleeai/codex-skills 里的技能安装到本地」。也可以只指定你需要的技能名。
2. 安装完成后，在下一轮对话中点名技能并描述任务。例如「用 `vllm-perf-experiments` 分析这份 AISBench CSV，找出满足 TPOT 目标的并发点」。
3. 仓库更新后，可以让 Codex 从 GitHub 下载新版并更新本地技能。手动安装时，请复制整个技能目录，以便保留其中的脚本、模板和参考资料。

## 查历史案例

[共享知识库](knowledge-base/README.md) 保存了 vLLM 性能测试的实验条件、逐轮数据、汇总 CSV 和复盘。想了解一次实验如何选点，可以从 [DeepSeek-V4-Pro 性能案例](knowledge-base/vllm-performance/artifacts/deepseek-v4-pro-20261106/README.md) 开始。复用结果前，先对照自己的模型、软件版本、硬件和负载。

只想浏览案例时，可以直接在 GitHub 打开。要用技能附带的工具检索知识库，请检出完整仓库，并按知识库说明配置 `CODEX_SKILLS_REPO`。
