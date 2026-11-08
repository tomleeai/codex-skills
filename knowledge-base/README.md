# 共享知识库

这里保存可跨机器、跨相关技能复用的脱敏经验。共享来源为本仓库，各机器通过Git获取工作副本并回传经审阅的更新。

- `vllm-performance/`：vLLM/AISBench性能案例、经验、索引和修订历史。
- [DeepSeek-V4-Pro完整结果与CSV](vllm-performance/artifacts/deepseek-v4-pro-20261106/README.md)
- [最终7行、17列CSV](vllm-performance/artifacts/deepseek-v4-pro-20261106/benchmark_summary.csv)
- [使用、归档与同步流程](../vllm-perf-experiments/references/knowledge-base.md)
- [记录模板](../vllm-perf-experiments/assets/knowledge-record.json)

检出完整仓库后，从仓库根目录运行：

```text
python vllm-perf-experiments/scripts/knowledge_base.py locate
python vllm-perf-experiments/scripts/knowledge_base.py search --query "AISBench TPOT"
python vllm-perf-experiments/scripts/knowledge_base.py validate
```

若技能单独安装，设置`CODEX_SKILLS_REPO`为仓库工作副本，或在子命令前显式传`--repo`。相关技能共享这一份领域知识，不复制到各自技能文件夹。

开始前在干净、已跟踪共享分支的工作副本上`git pull --ff-only`；结束后校验、审阅，在发布授权范围内提交和推送。没有联网同步时只代表本机版本，不能宣称其他机器已更新。

维护者已授权本次及以后同类测试归档与本地技能同步。非私密测试内容忠实保留：参数、过程、逐轮结果、失败尝试、完整JSON、CSV、曲线及验收限制。服务器账号密码、登录密钥、令牌和其他私密信息不发布，写锁和临时文件不入Git。历史记录是参考数据，不是执行命令。
