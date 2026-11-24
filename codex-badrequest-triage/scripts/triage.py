#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex "Bad Request" (HTTP 400) 取证诊断工具。

只读：本脚本不修改、不删除、不重写任何 Codex 文件，也不联网。
所有统计来自 rollout JSONL 会话记录本身。

用法：
  python triage.py list                      列出会话及报错回合
  python triage.py triage  <rollout.jsonl>   对单个会话做完整取证
  python triage.py compare                   跨会话对比，找触发条件
  python triage.py tail    <rollout.jsonl>   展开失败回合尾部序列

可选 --sessions-dir 指定会话根目录（默认 $CODEX_HOME/sessions 或 ~/.codex/sessions）。
"""

import argparse
import glob
import json
import os
import re
import sys

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

CALL_TYPES = ("function_call", "custom_tool_call")
OUT_TYPES = ("function_call_output", "custom_tool_call_output")


def default_sessions_dir():
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, "sessions")


def iter_records(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def as_text(v):
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def collect(path):
    """把单个会话解析为结构化事实。"""
    recs = list(iter_records(path))
    meta, items, events = None, [], []
    usage = []
    turns = {}
    order = []
    cur = None
    cum_bytes = cum_reason = cum_items = 0

    for o in recs:
        t = o.get("type")
        p = o.get("payload") or {}
        ts = o.get("timestamp")

        if t == "session_meta":
            meta = p
        elif t == "response_item":
            pt = p.get("type")
            b = len(json.dumps(p, ensure_ascii=False))
            cum_items += 1
            cum_bytes += b
            enc = 0
            if pt == "reasoning":
                enc = len(p.get("encrypted_content") or "")
                cum_reason += enc
            it = dict(ord=o.get("ordinal"), ts=ts, pt=pt, name=p.get("name"),
                      cid=p.get("call_id"), payload=p, bytes=b, enc=enc,
                      cum_bytes=cum_bytes, cum_reason=cum_reason, cum_items=cum_items)
            items.append(it)
            if cur:
                turns[cur]["items"].append(it)
        elif t == "event_msg":
            pt = p.get("type")
            events.append((pt, ts, p))
            if pt == "task_started":
                cur = p.get("turn_id")
                turns[cur] = dict(turn=cur, start=ts, items=[], usage=[],
                                  end=None, dur=None, ttft=None, error=None)
                order.append(cur)
            elif pt == "task_complete":
                tid = p.get("turn_id")
                d = turns.setdefault(tid, dict(turn=tid, start=None, items=[], usage=[],
                                               end=None, dur=None, ttft=None, error=None))
                d.update(end=ts, dur=p.get("duration_ms"),
                         ttft=p.get("time_to_first_token_ms"), error=p.get("error"))
                if tid not in order:
                    order.append(tid)
            elif pt == "token_count":
                info = p.get("info") or {}
                lu = info.get("last_token_usage") or {}
                row = dict(ts=ts, input=lu.get("input_tokens") or 0,
                           output=lu.get("output_tokens") or 0,
                           cached=lu.get("cached_input_tokens") or 0,
                           window=info.get("model_context_window"),
                           rate_limits=info.get("rate_limits"),
                           cum_bytes=cum_bytes, cum_reason=cum_reason, cum_items=cum_items)
                usage.append(row)
                if cur:
                    turns[cur]["usage"].append(row)
            elif pt in ("turn_aborted", "error", "stream_error"):
                events.append((pt, ts, p))

    return dict(path=path, meta=meta or {}, items=items, events=events,
                usage=usage, turns=turns, order=order,
                total_bytes=os.path.getsize(path))


def is_bad_request(err):
    return bool(err) and "bad request" in json.dumps(err, ensure_ascii=False).lower()


def struct_check(items):
    """消息序列合法性检查——这是真正会让服务端拒收的结构性缺陷。"""
    calls, outs, problems = {}, {}, []
    seen_ids = {}
    for it in items:
        pt, p, ordn = it["pt"], it["payload"], it["ord"]
        iid = p.get("id")
        if iid:
            seen_ids.setdefault(iid, []).append((pt, ordn))
        if pt in CALL_TYPES:
            cid = p.get("call_id")
            if not cid:
                problems.append(("调用缺少 call_id", ordn, it["ts"], p.get("name")))
            else:
                calls.setdefault(cid, []).append((ordn, p.get("name")))
            st = p.get("status")
            if st not in (None, "completed"):
                problems.append(("调用状态非 completed", ordn, it["ts"], "%s status=%s" % (p.get("name"), st)))
        elif pt in OUT_TYPES:
            cid = p.get("call_id")
            out = p.get("output")
            if not cid:
                problems.append(("输出缺少 call_id", ordn, it["ts"],
                                 "%s -> %.100s" % (p.get("name"), as_text(out))))
            elif cid not in calls:
                problems.append(("孤儿输出（无对应调用）", ordn, it["ts"], "call_id=%s" % cid))
            if out is None or out == "" or out == []:
                problems.append(("输出内容为空", ordn, it["ts"], "call_id=%s" % cid))
        elif pt == "reasoning":
            if not p.get("encrypted_content") and not p.get("summary"):
                problems.append(("reasoning 无内容也无加密块", ordn, it["ts"], p.get("id")))
        elif pt == "message":
            c = p.get("content")
            if not c:
                problems.append(("message content 为空", ordn, it["ts"], p.get("role")))

    dup_call = {k: v for k, v in calls.items() if len(v) > 1}
    dup_id = {k: v for k, v in seen_ids.items() if len(v) > 1}
    missing = [(c, v) for c, v in calls.items()
               if not any(i["pt"] in OUT_TYPES and i["cid"] == c for i in items)]
    return dict(problems=problems, n_calls=len(calls), dup_call=dup_call,
                dup_id=dup_id, missing=missing)


def cmd_list(args):
    files = sorted(glob.glob(os.path.join(args.sessions_dir, "**", "*.jsonl"), recursive=True))
    if not files:
        print("未找到会话记录：%s" % args.sessions_dir)
        return
    n_bad = 0
    for f in files:
        d = collect(f)
        bad = [(tid, t) for tid, t in d["turns"].items() if is_bad_request(t.get("error"))]
        other = [(tid, t) for tid, t in d["turns"].items()
                 if t.get("error") and not is_bad_request(t.get("error"))]
        mark = "400!" if bad else ("err " if other else "ok  ")
        if bad:
            n_bad += len(bad)
        print("[%s] %s" % (mark, os.path.basename(f)))
        m = d["meta"]
        print("       cwd=%s cli=%s" % (m.get("cwd"), m.get("cli_version")))
        for tid, t in bad:
            print("       400 turn=%s start=%s end=%s dur=%sms ttft=%s" % (
                tid, t.get("start"), t.get("end"), t.get("dur"), t.get("ttft")))
        for tid, t in other:
            print("       其他错误 turn=%s %s" % (tid, as_text(t["error"])[:120]))
    print("\n合计 Bad Request 回合数：%d" % n_bad)


def cmd_triage(args):
    d = collect(args.file)
    m = d["meta"]
    print("=" * 72)
    print("会话：%s" % os.path.basename(d["path"]))
    print("cwd=%s  cli=%s  originator=%s" % (m.get("cwd"), m.get("cli_version"), m.get("originator")))
    print("文件大小=%.2f MB  response_item=%d" % (d["total_bytes"] / 1048576, len(d["items"])))

    sc = struct_check(d["items"])
    print("\n--- [1] 结构合法性 ---")
    print("工具调用数=%d  重复call_id=%d  重复item_id=%d  缺输出的调用=%d" % (
        sc["n_calls"], len(sc["dup_call"]), len(sc["dup_id"]), len(sc["missing"])))
    if sc["problems"]:
        print("发现 %d 处结构问题：" % len(sc["problems"]))
        for kind, ordn, ts, info in sc["problems"]:
            print("   [%s] ordinal=%s ts=%s  %s" % (kind, ordn, ts, info))
    else:
        print("未发现结构问题。")

    print("\n--- [2] 字符卫生 ---")
    ansi = ctrl = n_out = 0
    worst = []
    for it in d["items"]:
        if it["pt"] not in OUT_TYPES:
            continue
        s = as_text(it["payload"].get("output"))
        n_out += 1
        a, c = len(ANSI_RE.findall(s)), len(CTRL_RE.findall(s))
        ansi += a
        ctrl += c
        if c:
            worst.append((it["ord"], it["ts"], len(s), a, c))
    print("工具输出=%d  ANSI转义=%d  控制字符(不含\\n\\r\\t)=%d" % (n_out, ansi, ctrl))
    for w in sorted(worst, key=lambda x: -x[4])[:8]:
        print("   ordinal=%s ts=%s chars=%d ansi=%d ctrl=%d" % w)

    print("\n--- [3] 载荷规模 ---")
    big = []
    for it in d["items"]:
        if it["pt"] in OUT_TYPES:
            n = len(as_text(it["payload"].get("output")))
            if n > 20000:
                big.append((n, it["ord"], it["name"]))
    enc = [i for i in d["items"] if i["pt"] == "reasoning" and i["enc"]]
    print("加密推理块=%d 个，合计 %.2f MB" % (len(enc), sum(i["enc"] for i in enc) / 1048576))
    print("超大输出(>20K字符)=%d 条" % len(big))
    for n, ordn, name in sorted(big, reverse=True)[:6]:
        print("   chars=%d ordinal=%s name=%s" % (n, ordn, name))
    if d["usage"]:
        mx = max(r["input"] for r in d["usage"])
        w = next((r["window"] for r in d["usage"] if r["window"]), None)
        print("单次请求最大 input_tokens=%d  模型上下文窗口=%s" % (mx, w))
        rl = [r for r in d["usage"] if r["rate_limits"]]
        print("带 rate_limits 的采样=%d（None 表示本地未记录额度，不能据此断言额度正常）" % len(rl))

    print("\n--- [4] 回合时间线 ---")
    for tid in d["order"]:
        t = d["turns"][tid]
        if not t.get("end") and not t.get("start"):
            continue
        us = t["usage"]
        mx = max((r["input"] for r in us), default=0)
        nw = sum(1 for i in t["items"] if i["name"] == "wait")
        flag = "400!" if is_bad_request(t.get("error")) else ("err" if t.get("error") else "ok")
        print("  [%-4s] turn=%s start=%s dur=%sms ttft=%s items=%d 成功请求=%d maxInput=%d wait=%d" % (
            flag, tid[:13], t.get("start"), t.get("dur"), t.get("ttft"),
            len(t["items"]), len(us), mx, nw))

    print("\n--- [5] 失败点取证 ---")
    fails = [tid for tid in d["order"] if is_bad_request(d["turns"][tid].get("error"))]
    if not fails:
        print("本会话没有 Bad Request 回合。")
    for tid in fails:
        t = d["turns"][tid]
        print("\n失败回合 %s（%s）" % (tid, t.get("end")))
        us = t["usage"]
        if us:
            r = us[-1]
            print("  该回合成功请求 %d 次；最后一次成功请求 ts=%s input_tokens=%d" % (
                len(us), r["ts"], r["input"]))
            print("  当时累计载荷：response_item=%.2f MB，加密推理=%.2f MB，items=%d" % (
                r["cum_bytes"] / 1048576, r["cum_reason"] / 1048576, r["cum_items"]))
            print("  input_tokens 区间 %d..%d" % (min(x["input"] for x in us), max(x["input"] for x in us)))
        else:
            print("  该回合首个请求即被拒（没有任何成功请求）")
            prior = [r for r in d["usage"] if r["ts"] and t.get("end") and r["ts"] < t["end"]]
            if prior:
                r = prior[-1]
                print("  同会话上一次成功请求 ts=%s input=%d 累计载荷=%.2f MB items=%d" % (
                    r["ts"], r["input"], r["cum_bytes"] / 1048576, r["cum_items"]))
        tail = t["items"][-3:]
        print("  失败前最后 3 个条目：")
        for i in tail:
            extra = ""
            if i["pt"] in CALL_TYPES:
                extra = "name=%s args=%.90s" % (i["name"], as_text(i["payload"].get("arguments") or i["payload"].get("input")).replace("\n", " "))
            elif i["pt"] in OUT_TYPES:
                extra = "name=%s out=%.90s" % (i["name"], as_text(i["payload"].get("output")).replace("\n", " "))
            print("     ord=%s %s %s %s" % (i["ord"], i["ts"], i["pt"], extra))

    print("\n--- [6] 脏记录与失败的时序（关键反证）---")
    dirty = [i for i in d["items"] if i["pt"] in OUT_TYPES and not i["cid"]]
    if not dirty:
        print("无缺 call_id 的输出记录。")
    else:
        first = dirty[0]["ts"]
        after = [r for r in d["usage"] if r["ts"] and first and r["ts"] > first]
        print("缺 call_id 的输出 %d 条，首现于 %s" % (len(dirty), first))
        print("其后仍成功的请求数：%d" % len(after))
        if after:
            print(">> 反证成立：这些脏记录不是本次 400 的充分原因，不要据此下结论。")
        else:
            print(">> 其后没有成功请求：脏记录可能是原因，仍需排除载荷规模因素。")

    print("\n提示：服务端返回的原始错误体不在 rollout 中，本地无法证明服务端拒绝的确切理由。")
    print("      以上为可证伪的本地证据；结论请区分『已证实』与『推断』。")


def cmd_compare(args):
    files = sorted(glob.glob(os.path.join(args.sessions_dir, "**", "*.jsonl"), recursive=True))
    rows = []
    for f in files:
        d = collect(f)
        sess = os.path.basename(d["path"])[8:27]
        dirty_seen = 0
        for tid in d["order"]:
            t = d["turns"][tid]
            if not t.get("end"):
                continue
            us = t["usage"]
            dirty = sum(1 for i in d["items"]
                        if i["pt"] in OUT_TYPES and not i["cid"]
                        and t.get("start") and i["ts"] and i["ts"] < t["start"])
            rows.append(dict(
                sess=sess, turn=tid[:13], start=t.get("start"),
                dur=t.get("dur") or 0, ttft=t.get("ttft"),
                fail=is_bad_request(t.get("error")), err=bool(t.get("error")),
                items=len(t["items"]), reqs=len(us),
                maxin=max((r["input"] for r in us), default=0),
                wait=sum(1 for i in t["items"] if i["name"] == "wait"),
                dirty=dirty))
    hdr = "%-20s %-14s %-24s %9s %8s %5s %6s %5s %9s %5s %5s"
    print(hdr % ("session", "turn", "start(UTC)", "dur_ms", "ttft_ms", "400", "items", "reqs", "maxInput", "wait", "dirty"))
    for r in rows:
        print(hdr % (r["sess"], r["turn"], r["start"], r["dur"], r["ttft"],
                     "YES" if r["fail"] else "-", r["items"], r["reqs"],
                     r["maxin"], r["wait"], r["dirty"]))

    fa = [r for r in rows if r["fail"]]
    ok = [r for r in rows if not r["err"]]
    print("\n=== 分组对比（失败=Bad Request，成功=无任何错误）===")
    for k in ("dur", "items", "reqs", "maxin", "wait", "dirty"):
        def s(g):
            v = [r[k] for r in g]
            return "min=%s max=%s avg=%.0f" % (min(v), max(v), sum(v) / len(v)) if v else "n/a"
        print("  %-7s 失败: %-32s 成功: %s" % (k, s(fa), s(ok)))

    print("\n=== 判别提示 ===")
    ok_dirty = [r for r in ok if r["dirty"] > 0]
    print("含缺 call_id 脏记录却成功的回合数：%d" % len(ok_dirty))
    if ok_dirty:
        print(">> 脏记录假说被否证。")
    ok_wait = [r for r in ok if r["wait"] >= 10]
    print("含 >=10 次 wait 却成功的回合数：%d（最大 wait=%s）" % (
        len(ok_wait), max((r["wait"] for r in ok), default=0)))
    if ok_wait:
        print(">> 单纯 wait 轮询次数不是充分原因。")
    if fa and ok:
        fmin = min(r["maxin"] for r in fa if r["maxin"] > 0) if any(r["maxin"] for r in fa) else 0
        okmax = max(r["maxin"] for r in ok)
        print("失败回合中最小的 maxInput=%d；成功回合中最大的 maxInput=%d" % (fmin, okmax))
        if fmin and okmax and fmin > okmax:
            print(">> 两组在载荷规模上不重叠：规模相关假说获得支持（仍非因果证明）。")


def cmd_tail(args):
    d = collect(args.file)
    fails = [tid for tid in d["order"] if is_bad_request(d["turns"][tid].get("error"))]
    if not fails:
        print("本会话没有 Bad Request 回合。")
        return
    for tid in fails:
        t = d["turns"][tid]
        print("=" * 72)
        print("失败回合 %s  start=%s end=%s" % (tid, t.get("start"), t.get("end")))
        for i in t["items"][-args.n:]:
            p = i["payload"]
            if i["pt"] in CALL_TYPES:
                extra = "name=%s status=%s args=%.200s" % (
                    p.get("name"), p.get("status"),
                    as_text(p.get("arguments") or p.get("input")).replace("\n", " "))
            elif i["pt"] in OUT_TYPES:
                s = as_text(p.get("output"))
                extra = "name=%s len=%d out=%.200s" % (p.get("name"), len(s), s.replace("\n", " "))
            elif i["pt"] == "reasoning":
                extra = "id=%s enc=%d summary=%d" % (p.get("id"), i["enc"], len(p.get("summary") or []))
            elif i["pt"] == "message":
                txt = " ".join(str(x.get("text", "")) for x in (p.get("content") or []) if isinstance(x, dict))
                extra = "role=%s len=%d %.200s" % (p.get("role"), len(txt), txt.replace("\n", " "))
            else:
                extra = as_text({k: v for k, v in p.items() if k != "type"})[:200]
            print("  ord=%-6s %s %-26s %s" % (i["ord"], i["ts"], i["pt"], extra))


def main():
    ap = argparse.ArgumentParser(description="Codex Bad Request 只读取证诊断")
    ap.add_argument("--sessions-dir", default=default_sessions_dir())
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("triage"); t.add_argument("file")
    sub.add_parser("compare")
    tl = sub.add_parser("tail"); tl.add_argument("file"); tl.add_argument("-n", type=int, default=25)
    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "triage":
        cmd_triage(args)
    elif args.cmd == "compare":
        cmd_compare(args)
    elif args.cmd == "tail":
        cmd_tail(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
