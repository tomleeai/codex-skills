# rollout 会话记录结构参考

路径：`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<本地时间>-<sessionid>[_<turnid>].jsonl`

每行一个 JSON 对象。**文件名用本地时区，内部 `timestamp` 用 UTC**（东八区差 8 小时）。比对时间前先换算，否则会定位到错误的回合。

同一会话可能拆成多个文件：带 `_<turnid>` 后缀的是该回合的分片。判断"最近一次失败"要看 `task_complete.error`，不能只看文件名最新——实测最新那个文件反而是成功的。

## 顶层记录类型

| type | 用途 |
|---|---|
| `session_meta` | `cwd`、`cli_version`、`originator`、`source`、会话 id |
| `turn_context` | 该回合的 `model`、`cwd`、`model_reasoning_effort` |
| `response_item` | **实际发给模型的条目**，取证主战场 |
| `event_msg` | 回合生命周期与用量事件 |
| `token_usage_record` | 用量记录 |
| `world_state` | 环境状态快照 |

## response_item.payload.type

- `message` — `role`（user/assistant）+ `content` 数组
- `reasoning` — `encrypted_content`（加密推理块，长度可达成百 KB）、`summary`
- `function_call` / `custom_tool_call` — `name`、`call_id`、`arguments`/`input`、`status`
- `function_call_output` / `custom_tool_call_output` — `call_id`、`output`

**配对规则**：每条 output 的 `call_id` 必须能匹配到前面的 call。缺 `call_id`、孤儿 output、重复 `call_id` 都是结构缺陷。

注意：`codex_apps_*` 这类由客户端自动注入的 output **可能没有 `call_id`**，但实测服务端容忍（见已否证假说 1）。发现它不等于找到原因。

## event_msg.payload.type 关键项

- `task_started` — `turn_id`，回合起点
- `task_complete` — `turn_id`、`duration_ms`、`time_to_first_token_ms`、`error`
  - **`error` 非空才是失败**。400 的形态：`{"message":"{\"detail\":\"Bad Request\"}","codex_error_info":"other"}`
- `turn_aborted` — `reason: interrupted` 表示**用户手动打断，不是 400**，别误判
- `token_count` — `info.last_token_usage.input_tokens`（该次请求实际输入）、`info.model_context_window`、`info.rate_limits`
- `thread_settings_applied` — `model`、`service_tier`、`approval_policy`

## 判读要点

**`time_to_first_token_ms`（ttft）是最有判别力的单一字段：**

| ttft | duration | 含义 | 止损 |
|---|---|---|---|
| `None` | 1–2 秒 | 回合**首个请求**即被拒，历史已损坏 | 开新对话；或经授权清理非法条目 |
| 正常值 | 很长 | 中途某次请求被拒 | 先原样重试一次 |

**`token_count` 事件数 ≈ 该回合成功请求数**。每个事件对应一次成功往返；失败那次不产生事件。据此可算出"失败前最后一次成功请求"的载荷规模。

**累计载荷**要自己按 `response_item` 顺序累加 `len(json.dumps(payload))`，记录里没有现成字段。加密推理块单独累加 `len(encrypted_content)`。

## 桌面端日志

Windows：`%LOCALAPPDATA%\Packages\OpenAI.Codex_<hash>\LocalCache\Local\Codex\Logs\YYYY\MM\DD\*.log`

实测：**不含服务端返回的错误体**，搜 `Bad Request` 命中 0。里面主要是 UI 事件、IPC、窗口状态。不要在这里找根因。

`~/.codex/logs_2.sqlite` 同样只有 migrations 表，无报错记录。

## 读取注意

- 单行可达数十万字符。用脚本按行 `json.loads` 解析，别用文本搜索工具直接匹配（长行会被截断或跳过，导致漏判）。
- PowerShell 处理这些记录容易在空字段上抛 `NullArrayIndex`；用 Python 更稳。
- 只读打开，不要改写。任何清理操作都需用户明确同意 + 先备份。
