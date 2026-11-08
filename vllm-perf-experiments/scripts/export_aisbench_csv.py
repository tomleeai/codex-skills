#!/usr/bin/env python3
"""Export already-selected AISBench dictionaries to the 17-column case template.

Selection and log validation belong upstream. Only allowlisted numeric metrics
and a generated workload label enter the CSV; environment fields are ignored.
"""

import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile


HEADERS = [
    "输入+输出", "TPOT", "prefix cache", "实测总请求数", "实测最大并发数",
    "实测系统并发数", "实测请求频率(req/s)", "实测TTFT平均(ms)",
    "实测TTFT P90(ms)", "实测TPOT平均(ms)", "实测TPOT   SLO_P90(ms)",
    "实测E2EL时间(s)", "实测输出吞吐(tps)", "实测单卡输出吞吐(tps)",
    "实测E2E吞吐(tps)/端到端时延(s)", "实测单卡E2E吞吐(tps)", "实测QPS",
]


def number(value, field, *, required=False, integer=False):
    if value is None or value == "":
        if required:
            raise ValueError(f"Missing required metric: {field}")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"Invalid numeric metric: {field}")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"Invalid numeric metric: {field}") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Metric must be finite and nonnegative: {field}")
    if integer and not result.is_integer():
        raise ValueError(f"Count must be an integer: {field}")
    return int(result) if result.is_integer() else result


def length_label(value):
    return f"{value // 1024}k" if isinstance(value, int) and value % 1024 == 0 else str(value)


def build_rows(selected, *, time_metric="request-mean", request_frequency="measured",
               per_card="blank", card_count=None):
    if time_metric not in {"request-mean", "batch-duration"}:
        raise ValueError("Unsupported time metric")
    if request_frequency not in {"measured", "raw"}:
        raise ValueError("Unsupported request frequency mode")
    if per_card not in {"blank", "raw", "normalize"}:
        raise ValueError("Unsupported per-card mode")
    if per_card == "normalize":
        card_count = number(card_count, "card_count", required=True, integer=True)
        if card_count < 1:
            raise ValueError("Normalization requires a positive verified card count")
    elif card_count is not None:
        raise ValueError("card_count is only used with per_card=normalize")
    if not isinstance(selected, list) or not selected:
        raise ValueError("Expected a nonempty list of selected result dictionaries")
    rows = []
    for item in selected:
        if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
            raise ValueError("Each selected item must contain a result dictionary")
        raw = item["result"]
        get = lambda key, **kw: number(raw.get(key), key, **kw)
        input_len = number(item.get("input_len"), "input_len", required=True)
        output_len = number(item.get("output_len", raw.get("output_len")), "output_len", required=True)
        if input_len <= 0 or output_len <= 0:
            raise ValueError("Input and output lengths must be positive")
        target = number(item.get("TPOT_target_ms"), "TPOT_target_ms", required=True)
        prefix = number(item.get("nominal_prefix_ratio"), "nominal_prefix_ratio", required=True)
        if target <= 0 or prefix > 1:
            raise ValueError("TPOT target must be positive and prefix ratio must be within 0..1")
        time_key = "E2EL avg" if time_metric == "request-mean" else "E2E_time"
        duration = get(time_key)
        if duration is not None and time_metric == "request-mean":
            duration /= 1000
        output_tps, e2e_tps = get("output_throughput"), get("E2E_throughput")
        if per_card == "raw":
            single_output, single_e2e = get("single_output_throughput"), get("single_E2E_throughput")
        elif per_card == "normalize":
            single_output = None if output_tps is None else output_tps / card_count
            single_e2e = None if e2e_tps is None else e2e_tps / card_count
        else:
            single_output = single_e2e = None
        rows.append([
            f"{length_label(input_len)}+{length_label(output_len)}", target, prefix,
            get("total_req", integer=True), get("max_cc", integer=True), get("cc"),
            get("qps" if request_frequency == "measured" else "rr"),
            get("TTFT avg"), get("TTFT P90"), get("TPOT avg"), get("TPOT SLO_P90"),
            duration, output_tps, single_output, e2e_tps, single_e2e, get("qps"),
        ])
    return rows


def atomic_write(destination, content, encoding):
    destination.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline="",
                                         dir=destination.parent, delete=False) as stream:
            name = stream.name
            stream.write(content)
        os.replace(name, destination)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-metric", choices=["request-mean", "batch-duration"], default="request-mean")
    parser.add_argument("--request-frequency", choices=["measured", "raw"], default="measured")
    parser.add_argument("--per-card", choices=["blank", "normalize", "raw"], default="blank")
    parser.add_argument("--card-count", type=int)
    parser.add_argument("--metadata-output", type=Path)
    args = parser.parse_args(argv)
    paths = [args.source.resolve(), args.output.resolve()]
    if args.metadata_output:
        paths.append(args.metadata_output.resolve())
    if len(set(paths)) != len(paths):
        parser.error("Source, output and metadata paths must be distinct")
    try:
        selected = json.loads(args.source.read_text(encoding="utf-8-sig"))
        rows = build_rows(selected, time_metric=args.time_metric,
                          request_frequency=args.request_frequency,
                          per_card=args.per_card, card_count=args.card_count)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(HEADERS)
        writer.writerows(rows)
        csv_text = buffer.getvalue()
        # All free text is generated here; source identities never enter output.
        atomic_write(args.output, csv_text, "utf-8-sig")
        with args.output.open(encoding="utf-8-sig", newline="") as stream:
            actual = list(csv.reader(stream))
        expected = [["" if value is None else str(value) for value in row] for row in [HEADERS, *rows]]
        if actual != expected:
            raise ValueError("Saved CSV verification failed")
        metadata = {
            "row_count": len(rows), "column_count": len(HEADERS), "encoding": "UTF-8 BOM",
            "time_column": "E2EL avg / 1000" if args.time_metric == "request-mean" else "E2E_time (batch duration, not request latency)",
            "request_frequency_column": "qps" if args.request_frequency == "measured" else "rr (configured arrival rate, not observed frequency)",
            "per_card_mode": args.per_card, "verified_card_count": args.card_count,
            "max_cc_note": "Copied from wrapper; may be configured maximum rather than observed peak",
            "prefix_note": "Nominal ratio; actual cache hit must be reported separately",
            "missing_metric_cells": sum(value is None for row in rows for value in row),
        }
        if args.metadata_output:
            atomic_write(args.metadata_output, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(json.dumps({"rows": len(rows), "columns": len(HEADERS), "encoding": "UTF-8 BOM",
                          "missing_metric_cells": metadata["missing_metric_cells"]}))
        return 0
    except (ValueError, OSError, UnicodeError):
        # Numeric validation messages contain only allowlisted field names.
        print("Export failed: check selected-result schema, numeric metrics, options and local file access.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
