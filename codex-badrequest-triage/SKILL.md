---
name: codex-badrequest-triage
description: "诊断 Codex 报错 {\"detail\":\"Bad Request\"}（HTTP 400）：从 rollout 会话记录取证，逐项否证或确认成因，区分已证实与推断，给出可执行的止损与预防。当 Codex 出现 Bad Request、400、回合莫名失败、重试仍失败、长任务跑到一半中断时使用。"
---

# Codex Bad Request 取证诊断

`{"detail":"Bad Request"}` 是**服务端拒收请求体**，不是网络问题、不是额度问题、不是用户操作问题。Codex 把它归类为 `codex_error_info: "other"`，界面上只留一句话，所以必须回到会话记录取证。

**本技能的第一原则：先否证，再下结论。** 这个报错的直觉解释几乎全是错的。见 [已否证假说清单](references/debunked-hypotheses.md)——那里记录了四个看起来极有说服力、但被本机真实数据推翻的假说。**不要重蹈覆辙。**

## 取证流程

工具只读，不改任何 Codex 文件，不联网：

```bash
python scripts/triage.py list                    # 哪些会话有 400
python scripts/triage.py triage <rollout.jsonl>  # 单会话六段取证
python scripts/triage.py compare                 # 跨会话对比，自动否证
python scripts/triage.py tail <rollout.jsonl>    # 失败回合尾部序列
```

会话记录位置：`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`（默认 `~/.codex/sessions`）。文件名时间是**本地时区**，记录内 `timestamp` 是 **UTC**，换算后再比对，否则会找错回合。

桌面端日志（Windows：`%LOCALAPPDATA%\Packages\OpenAI.Codex_*\LocalCache\Local\Codex\Logs\`）**不含服务端返回的错误体**。已实测确认：搜不到 `Bad Request` 原文。不要在日志里空转。

### 判读顺序

1. **先定位真正的失败回合**。`turn_aborted / reason: interrupted` 是用户手动打断，**不是** 400，别混为一谈。只认 `task_complete.error` 里含 `Bad Request` 的回合。
2. **看 `ttft`（首字延迟）**。`ttft=None` 且 `duration` 只有 1–2 秒 → 该回合**第一个请求**就被拒；`ttft` 正常但跑了很久才失败 → 中途某次请求被拒。这两种性质完全不同，止损方式也不同。
3. **跑结构检查**。缺 `call_id` 的输出、孤儿输出、重复 `call_id`、空 `content` 才是真正会被服务端拒收的缺陷。
4. **做时序反证**（关键）。发现可疑记录后，**必须**检查它出现之后是否还有成功请求。有 → 该假说被否证，立即放弃，不要写进结论。
5. **跨会话对比**。用 `compare` 把失败组和成功组放在一起。任何"失败组有、成功组也有"的特征都不是充分原因。

## 已证实的事实边界

- 服务端返回的原始错误体**不落盘**。本地证据只能否证假说、缩小范围，**无法证明**服务端拒绝的确切理由。
- 因此结论必须分三档写：**已证实**（有直接记录）／**已否证**（有反例）／**推断**（无直证，标注为推断）。
- 缺证据时说"无法确定"，不要用直觉补一个像样的原因。宁可交付"排除了 A/B/C/D，剩余可能是 E，需要服务端日志"，也不要给一个错误的确定答案。

## 止损（按 ttft 分流）

**A. `ttft=None`、秒级失败 → 该对话已确定性损坏**
每次请求都会重放同样的非法历史，重试无用。
- 首选：开新对话，重新交代任务上下文。
- 想救原对话：剔除会话记录中的非法条目。**这是改写 Codex 自己的数据，必须先取得用户明确同意，先备份原文件，只删已定位的条目。** 未获同意不得动手。

**B. `ttft` 正常、长任务中途失败 → 单次请求被拒，对话未必坏**
先原样重试一次。仍失败再按 A 处理。同时降低单轮载荷（见下）。

## 预防

- **压缩单轮工具输出**。实测本机有单条输出接近 4 万字符。长日志用 `tail -n` / `grep` 截取，别整份回灌。
- **控制轮询链长度**。长时间等待远端就绪时，把多次 `wait` 合并成一次带较长 `yield_time_ms` 的等待，减少请求次数与历史条目。
- **避免 alpha 版**。本机 `0.167.0-alpha.4.1` 存在 Companion Page 注入缺 `call_id` 输出的缺陷（虽已证明不是 400 的充分原因，但确实是缺陷）。正式版更稳。
- **长任务分段**。单回合累积过多条目时主动开新对话续跑，别把整场实验压在一个回合里。

## 按需读取

- 四个被真实数据推翻的假说及其反例：[已否证假说清单](references/debunked-hypotheses.md)
- rollout 记录字段含义与判读要点：[记录结构参考](references/rollout-format.md)
