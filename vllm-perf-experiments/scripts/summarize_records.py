#!/usr/bin/env python3
"""Read selected validated run JSON, check observable invariants, and aggregate locally.

No SSH, service operations, inference requests, or raw-log parsing. Requires a
JSON object per run with valid=true; phase=measure (or *_measureN.json), config,
workload, ttft_ms, tpot_ms, tps, requests, success, failed. Optional fields follow
the Qwen case's public round schema. Missing optional evidence is reported rather
than invented. Exit 2 means malformed/invalid/duplicate selected records exist;
outputs retain the valid subset and rejection reasons, not a complete experiment.
"""
import argparse
import csv
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

IDENTITY = ('protocol_id', 'topology_id', 'host', 'device', 'placement', 'role', 'workload')
METRICS = ('ttft_ms', 'tpot_ms', 'tps', 'prefix_hit_rate', 'active_peak', 'waiting_peak', 'kv_peak')


def number(value, label):
    if isinstance(value, bool):
        raise ValueError(label + ' must be numeric')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(label + ' is not finite')
    return result


def normalize(raw, source, args):
    if not isinstance(raw, dict):
        raise ValueError('record must be a JSON object')
    if raw.get('formal') is False or raw.get('phase') in ('warmup', 'diagnostic', 'preflight'):
        return None, ['non-formal record excluded']
    if raw.get('phase') not in ('measure', 'formal') and not (
        raw.get('phase') is None and re.search(r'_measure\d+\.json$', source.name)
    ):
        raise ValueError('formal phase cannot be established')
    if raw.get('valid') is not True:
        raise ValueError('valid must be explicitly true')
    warnings = []
    row = {k: raw.get(k, 'unspecified') for k in IDENTITY}
    recorded_protocol = raw.get('protocol_id')
    if args.protocol_id and recorded_protocol and recorded_protocol != args.protocol_id:
        raise ValueError('recorded protocol_id conflicts with requested protocol; do not relabel experiments')
    row['protocol_id'] = recorded_protocol or args.protocol_id or 'selected:' + args.root.name
    row['topology_id'] = raw.get('topology_id', raw.get('mode', 'unspecified'))
    placement_evidence = bool(raw.get('placement')) or (bool(raw.get('host')) and raw.get('device') is not None)
    identity_verified = row['topology_id'] != 'unspecified' and placement_evidence
    row['placement_identity_verified'] = identity_verified
    if not identity_verified:
        # Anonymous runs must not silently become a same-device average or pair.
        row['placement'] = 'unverified-record:' + str(source.resolve())
        warnings.append('placement/topology identity incomplete; anonymous runs kept separate and unpaired')
    if not args.protocol_id and not raw.get('protocol_id'):
        warnings.append('protocol_id absent; input tree must represent one frozen protocol')
    row.update(config=str(raw['config']), workload=str(raw['workload']),
               repeat=raw.get('repeat', source.stem), source=str(source.resolve()), time=raw.get('time'))
    for key in ('requests', 'success', 'failed'):
        row[key] = number(raw[key], key)
        if row[key] < 0 or not row[key].is_integer():
            raise ValueError(key + ' must be a nonnegative integer')
    if row['requests'] <= 0 or row['failed'] != 0 or row['success'] != row['requests']:
        raise ValueError('not all logical client requests succeeded')
    if args.expected_requests is not None and row['requests'] != args.expected_requests:
        raise ValueError('logical client request count differs from expectation')
    for key in ('ttft_ms', 'tpot_ms', 'tps'):
        row[key] = number(raw[key], key)
        if row[key] < 0 or (key == 'tps' and row[key] == 0):
            raise ValueError('invalid performance metric ' + key)
    for key in METRICS[3:]:
        if key in raw and raw[key] is not None:
            row[key] = number(raw[key], key)
    if 'prefix_hit_rate' in row and not 0 <= row['prefix_hit_rate'] <= 1:
        raise ValueError('prefix_hit_rate must be a fraction in [0,1]')
    for key in ('seq', 'tokens', 'input_tokens', 'output_tokens', 'initial_cached_tokens',
                'initial_prefix_control_verified', 'runtime_prefix_target_met', 'warming_recipe'):
        if key in raw:
            row[key] = raw[key]
    if raw.get('runtime_prefix_target_met') is False:
        warnings.append('runtime prefix target unmet retained as outcome')
    delta = raw.get('metrics_delta', {})
    for source_key, target in [('vllm:num_preemptions_total', 'preemptions'),
                               ('vllm:request_success_total', 'server_successes')]:
        if source_key in delta:
            row[target] = number(delta[source_key], source_key)
            if row[target] < 0:
                raise ValueError('counter reset or negative delta: ' + source_key)
    if args.server_successes_per_request is not None:
        expected = row['requests'] * args.server_successes_per_request
        if 'server_successes' not in row or not math.isclose(row['server_successes'], expected):
            raise ValueError('server success delta does not match declared routing rule')
    else:
        warnings.append('server request accounting not asserted; PD routing may produce internal requests')
    if 'generated_tokens' in raw and 'duration_ms' in raw:
        generated = number(raw['generated_tokens'], 'generated_tokens')
        duration = number(raw['duration_ms'], 'duration_ms')
        if generated <= 0 or duration <= 0:
            raise ValueError('invalid generation count or duration')
        if not math.isclose(generated / (duration / 1000), row['tps'], rel_tol=args.tps_tolerance):
            raise ValueError('TPS differs from total output / declared common duration')
        row.update(generated_tokens=generated, duration_ms=duration)
    else:
        warnings.append('total-output TPS reconciliation evidence absent')
    warnings.append('per-request output lengths and external-traffic controls require native run evidence')
    if args.initial_cache_workload == row['workload']:
        if row.get('initial_prefix_control_verified') is not True:
            raise ValueError('initial cache verification failed or absent')
        if row.get('initial_cached_tokens') != args.expected_initial_cached_tokens:
            raise ValueError('initial cached tokens differ from protocol')
    return row, warnings


def group_identity(row):
    return tuple(str(row.get(k, 'unspecified')) for k in IDENTITY)


def aggregate(rows, baseline):
    groups = defaultdict(list)
    for row in rows:
        groups[group_identity(row) + (row['config'],)].append(row)
    result = []
    for key, members in sorted(groups.items()):
        summary = dict(zip(IDENTITY + ('config',), key), n=len(members),
                       placement_identity_verified=all(r['placement_identity_verified'] for r in members))
        for metric in METRICS:
            values = [r[metric] for r in members if metric in r]
            if values:
                summary.update({metric: statistics.mean(values), metric + '_n': len(values),
                                metric + '_sd': statistics.stdev(values) if len(values) > 1 else None,
                                metric + '_min': min(values), metric + '_max': max(values)})
        if any('preemptions' in r for r in members):
            summary['preemptions_total'] = sum(r.get('preemptions', 0) for r in members)
            summary['preemptions_n'] = sum('preemptions' in r for r in members)
        for metric in ('seq', 'tokens'):
            values = {str(r[metric]) for r in members if metric in r}
            if len(values) > 1:
                raise ValueError('one config label contains multiple ' + metric + ' values: ' + str(key))
            if values:
                summary[metric] = members[0].get(metric)
        result.append(summary)
    baselines = {group_identity(r): r for r in result if r['config'] == baseline}
    for row in result:
        base = baselines.get(group_identity(row)) if row['placement_identity_verified'] else None
        for metric in ('ttft_ms', 'tpot_ms', 'tps'):
            if base and base[metric] != 0:
                row[metric + '_vs_local_baseline_pct'] = 100 * (row[metric] / base[metric] - 1)
    return result


def write_json(path, value):
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path, help='explicitly selected frozen experiment tree')
    parser.add_argument('--glob', default='**/runs/*/*_measure[0-9]*.json', help='relative run record pattern; default selects native runs directories, not log-side JSON')
    parser.add_argument('--protocol-id', help='identity for records lacking it; conflicting recorded IDs are rejected')
    parser.add_argument('--expected-requests', type=int)
    parser.add_argument('--baseline', default='baseline', help='baseline config label, paired within identical placement/workload')
    parser.add_argument('--server-successes-per-request', type=float, help='only set after validating internal routing counts (coupled often 1)')
    parser.add_argument('--initial-cache-workload', help='workload for which exact initial cache is a declared control')
    parser.add_argument('--expected-initial-cached-tokens', type=int)
    parser.add_argument('--tps-tolerance', type=float, default=0.001, help='relative tolerance for TPS reconciliation')
    parser.add_argument('--output', type=Path, help='write public records.csv, aggregates.csv and summary.json locally')
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error('root is not a directory')
    if args.expected_requests is not None and args.expected_requests <= 0:
        parser.error('--expected-requests must be positive')
    if args.tps_tolerance <= 0:
        parser.error('--tps-tolerance must be positive')
    if (args.initial_cache_workload is None) != (args.expected_initial_cached_tokens is None):
        parser.error('both initial-cache options must be specified together')
    rows, rejected, skipped, warnings = [], [], [], Counter()
    sources = sorted(set(args.root.glob(args.glob)))
    if not sources:
        parser.error('no selected records matched')
    for source in sources:
        try:
            row, notes = normalize(json.loads(source.read_text(encoding='utf-8-sig')), source, args)
            warnings.update(notes)
            if row is None:
                skipped.append(str(source))
            else:
                rows.append(row)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append({'source': str(source), 'reason': str(exc)})
    by_round = defaultdict(list)
    for row in rows:
        by_round[group_identity(row) + (row['config'], str(row['repeat']))].append(row)
    duplicates = {r['source'] for members in by_round.values() if len(members) > 1 for r in members}
    rejected.extend({'source': s, 'reason': 'duplicate protocol/placement/config/workload/repeat identity'} for s in sorted(duplicates))
    rows = [r for r in rows if r['source'] not in duplicates]
    try:
        summaries = aggregate(rows, args.baseline)
    except ValueError as exc:
        rejected.append({'source': str(args.root), 'reason': str(exc)})
        summaries = []
    missing_pairs = sum('tps_vs_local_baseline_pct' not in row for row in summaries)
    if missing_pairs:
        warnings['local baseline absent or identity unverified; relative comparisons omitted'] += missing_pairs
    result = {'state': 'invalid_inputs' if rejected else 'aggregated_not_complete_validation',
              'selected': len(sources), 'valid_records': len(rows), 'excluded_nonformal': len(skipped),
              'requests': sum(r['requests'] for r in rows), 'groups': len(summaries),
              'warnings': dict(warnings), 'rejected': rejected, 'excluded_sources': skipped,
              'validation_scope': 'observable record checks only; native controls, expected rounds, recovery and output artifacts require separate verification',
              'aggregates': summaries}
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        write_csv(args.output / 'records.csv', rows)
        write_csv(args.output / 'aggregates.csv', summaries)
        write_json(args.output / 'summary.json', result)
    compact = {k: result[k] for k in ('state', 'selected', 'valid_records', 'excluded_nonformal', 'requests', 'groups')}
    compact.update(warning_types=len(warnings), warnings=dict(warnings),
                   validation_scope=result['validation_scope'], rejected=len(rejected), errors=rejected[:3])
    print(json.dumps(compact, ensure_ascii=False, separators=(',', ':')))
    return 2 if rejected else 0


if __name__ == '__main__':
    raise SystemExit(main())
