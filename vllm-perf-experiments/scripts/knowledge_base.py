#!/usr/bin/env python3
"""Repository-backed performance knowledge: locate, search, show, put, rebuild, validate.

No network calls or historical command execution. Input must be sanitized before
use; pattern screening below supplements, rather than replaces, local review.
"""

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


MAX_BYTES = 512_000
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
STATUSES = {"case": {"verified", "partial", "unresolved", "deprecated"},
            "lesson": {"verified", "candidate", "deprecated"}}
FIELDS = {"schema_version", "id", "kind", "title", "summary", "status",
          "created_at", "updated_at", "observed_at", "tags", "applies_to", "body",
          "evidence", "related_ids", "supersedes"}
IDENTITY_KEYS = {"password", "passwd", "secret", "api_key", "token", "private_key",
                 "hostname", "ip", "address", "username", "login", "container_id",
                 "serial_number", "asset_id", "vlan", "raw_log", "raw_chat"}
PATTERNS = {
    "ipv4": r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?\b",
    "ipv6": r"(?i)(?:\b[0-9a-f]{0,4}:){3,}[0-9a-f:]+",
    "account": r"[\w.+-]+@[\w.-]+",
    "token": r"\bsk-[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_.-]{20,}",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "credential": r"(?i)sshpass\s+-p|\b(?:password|passwd|passphrase|api[_-]?key|access_token|refresh_token|secret)\s*[:=]\s*[^\s<]",
    "private_path": r"(?i)(?<![a-z0-9])[a-z]:[\\/]|/(?:home|root|mnt|Users)/[^\s<]",
    "private_domain": r"(?i)\b[\w.-]+\.(?:corp|internal|local)\b",
    "url": r"(?i)https?://",
    "mac": r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b",
}


class KBError(Exception):
    """Only fixed codes, never rejected source values, enter tool output."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise KBError("duplicate_json_key")
        value[key] = item
    return value


def load_json(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise KBError("unsafe_or_oversized_file")
    def reject_constant(_):
        raise KBError("nonfinite_number")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def privacy_check(value):
    strings = []
    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key.casefold() in IDENTITY_KEYS:
                    raise KBError("identity_or_secret_field")
                strings.append(key)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            strings.append(item)
    walk(value)
    for text in strings:
        for label, pattern in PATTERNS.items():
            if re.search(pattern, text):
                raise KBError("privacy_" + label)


def valid_id(value):
    return isinstance(value, str) and len(value) <= 96 and ID.fullmatch(value)


def check_record(record):
    if not isinstance(record, dict) or set(record) != FIELDS or type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise KBError("record_schema")
    if not valid_id(record["id"]) or record["kind"] not in STATUSES:
        raise KBError("record_identity")
    if record["status"] not in STATUSES[record["kind"]]:
        raise KBError("record_status")
    for key in ("title", "summary"):
        if not isinstance(record[key], str) or not record[key].strip() or len(record[key]) > 800:
            raise KBError("record_summary")
    for key in ("created_at", "updated_at", "observed_at"):
        if not isinstance(record[key], str) or date.fromisoformat(record[key]).isoformat() != record[key]:
            raise KBError("record_date")
    if record["updated_at"] < record["created_at"]:
        raise KBError("date_order")
    for key in ("tags", "related_ids", "supersedes"):
        if not isinstance(record[key], list) or not all(isinstance(x, str) for x in record[key]):
            raise KBError("record_list")
    if not record["tags"] or not all(valid_id(x) for x in record["related_ids"] + record["supersedes"]):
        raise KBError("record_links")
    if record["id"] in record["supersedes"]:
        raise KBError("self_supersession")
    scope = record["applies_to"]
    if not isinstance(scope, dict) or set(scope) != {"models", "hardware", "software_versions", "workload_modes"}:
        raise KBError("applicability_schema")
    for key in ("models", "hardware", "workload_modes"):
        if not isinstance(scope[key], list) or not all(isinstance(x, str) for x in scope[key]):
            raise KBError("applicability_list")
    if not isinstance(scope["software_versions"], dict) or not all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in scope["software_versions"].items()):
        raise KBError("version_schema")
    if not isinstance(record["body"], dict) or not record["body"]:
        raise KBError("record_body")
    if not isinstance(record["evidence"], list) or not record["evidence"]:
        raise KBError("evidence_required")
    for evidence in record["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) - {"ref", "role", "sha256"} or not {"ref", "role"} <= set(evidence):
            raise KBError("evidence_schema")
        ref = evidence["ref"]
        if not isinstance(ref, str) or not isinstance(evidence["role"], str):
            raise KBError("evidence_type")
        if not re.fullmatch(r"(?:case:[a-z0-9-]+|artifact:[a-zA-Z0-9_][a-zA-Z0-9_./-]*|skill://[a-z0-9-]+/[a-zA-Z0-9_./-]+)", ref):
            raise KBError("evidence_reference")
        if ".." in ref or "//" in ref.removeprefix("skill://"):
            raise KBError("evidence_traversal")
        if "sha256" in evidence and not re.fullmatch(r"[a-f0-9]{64}", evidence["sha256"]):
            raise KBError("evidence_hash")
    canonical(record)
    privacy_check(record)
    return record


def read_records(root):
    records = {}
    for kind in STATUSES:
        directory = root / (kind + "s")
        if directory.is_symlink():
            raise KBError("unsafe_store_directory")
        for path in sorted(directory.glob("*.json")):
            record = check_record(load_json(path))
            if path.stem != record["id"] or kind != record["kind"] or record["id"] in records:
                raise KBError("store_identity_conflict")
            records[record["id"]] = record
    return records


def index_for(records):
    return {"schema_version": 1, "entries": [
        {**{key: r[key] for key in ("id", "kind", "title", "summary", "status", "tags", "applies_to", "updated_at")},
         "path": r["kind"] + "s/" + r["id"] + ".json", "revision": digest(r)}
        for _, r in sorted(records.items())]}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise KBError("unsafe_write_path")
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=".tmp-",
                                         dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


@contextmanager
def writer_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".write.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise KBError("writer_locked_inspect_locally_do_not_auto_delete") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def fingerprint(record):
    return digest({k: v for k, v in record.items() if k not in {"id", "created_at", "updated_at"}})


def put(root, record, expected=None):
    record = check_record(record)
    with writer_lock(root):
        records = read_records(root)
        old = records.get(record["id"])
        if old == record:
            # Also repair a missing/stale derived index without changing history.
            atomic_json(root / "index.json", index_for(records))
            return {"state": "unchanged", "id": record["id"], "revision": digest(record)}
        if old:
            if old["kind"] != record["kind"] or expected != digest(old):
                raise KBError("revision_conflict_supply_current_expected_revision")
            if old["created_at"] != record["created_at"] or record["updated_at"] < old["updated_at"]:
                raise KBError("revision_date_order")
        elif expected is not None:
            raise KBError("expected_revision_but_record_missing")
        if any(fingerprint(r) == fingerprint(record) for key, r in records.items() if key != record["id"]):
            raise KBError("duplicate_content_use_existing_record")
        for item in ([old] if old else []) + [record]:
            archive = root / "revisions" / record["id"] / (digest(item) + ".json")
            if not archive.exists():
                atomic_json(archive, item)
            elif load_json(archive) != item:
                raise KBError("revision_archive_conflict")
        atomic_json(root / (record["kind"] + "s") / (record["id"] + ".json"), record)
        records[record["id"]] = record
        atomic_json(root / "index.json", index_for(records))
        return {"state": "updated" if old else "created", "id": record["id"], "revision": digest(record)}


def index_state(root, records):
    path = root / "index.json"
    if not path.exists():
        return "missing_using_validated_records"
    try:
        return "current" if load_json(path) == index_for(records) else "stale_using_validated_records"
    except (KBError, ValueError, OSError):
        return "invalid_using_validated_records"


def skill_base(repository):
    """Support skills at repository root or under a conventional skills folder."""
    for base in (repository, repository / "skills"):
        if (base / "vllm-perf-experiments" / "SKILL.md").is_file():
            return base
    raise KBError("repository_skill_layout_not_found")


def paired_repository(root):
    if root.name == "vllm-performance" and root.parent.name == "knowledge-base":
        repository = root.parent.parent
        if not (repository / ".git").exists():
            return None
        try:
            skill_base(repository)
            return repository
        except KBError:
            pass
    return None


def resolve_store(args, codex_home):
    if args.root and args.repo:
        raise KBError("choose_root_or_repo_not_both")
    if args.root:
        root = args.root.expanduser().resolve()
        return root, paired_repository(root)
    configured = args.repo or os.environ.get("CODEX_SKILLS_REPO")
    if configured:
        repository = Path(configured).expanduser().resolve()
        if not (repository / ".git").exists():
            raise KBError("repository_is_not_a_git_checkout")
        skill_base(repository)
        return repository / "knowledge-base" / "vllm-performance", repository
    # An in-repository tool should use the same checkout as its evidence.
    for repository in Path(__file__).resolve().parents:
        root = repository / "knowledge-base" / "vllm-performance"
        if root.is_dir() and paired_repository(root) == repository:
            return root, repository
    repository = codex_home / "repos" / "codex-skills"
    root = repository / "knowledge-base" / "vllm-performance"
    if root.is_dir() and paired_repository(root) == repository:
        return root, repository
    # Do not silently create a machine-only database when shared storage is absent.
    raise KBError("shared_repository_not_configured_use_repo_or_CODEX_SKILLS_REPO")


def evidence_checks(record, records, codex_home, repository=None):
    issues = []
    if any(key not in records for key in record["related_ids"] + record["supersedes"]):
        issues.append("missing_related_record")
    for evidence in record["evidence"]:
        ref = evidence["ref"]
        if ref.startswith("case:"):
            linked = records.get(ref[5:])
            if not linked or linked["kind"] != "case":
                issues.append("missing_case_evidence")
            elif linked["status"] == "deprecated":
                issues.append("source_case_deprecated")
            elif "sha256" in evidence and digest(linked) != evidence["sha256"]:
                issues.append("case_evidence_changed")
        elif ref.startswith("skill://"):
            relative = ref[8:]
            base = skill_base(repository) if repository else codex_home / "skills"
            path = base / relative
            if not path.resolve().is_relative_to(base.resolve()) or not path.is_file():
                issues.append("skill_evidence_unavailable")
            elif "sha256" in evidence and hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                issues.append("skill_evidence_changed")
        elif repository:
            base = repository / "knowledge-base" / "vllm-performance" / "artifacts"
            path = base / ref.removeprefix("artifact:")
            if not path.resolve().is_relative_to(base.resolve()) or not path.is_file():
                issues.append("artifact_evidence_unavailable")
            elif "sha256" in evidence and hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                issues.append("artifact_evidence_changed")
        else:
            issues.append("artifact_reference_requires_project_evidence")
    return sorted(set(issues))


def terms(query):
    latin = re.findall(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*", query.casefold())
    chinese = re.findall(r"[\u3400-\u9fff]+", query)
    return set(latin + [x for word in chinese for x in ([word] + [word[i:i+2] for i in range(len(word)-1)])])


def search(records, args, codex_home, repository=None):
    query_terms = terms(args.query)
    if not query_terms:
        raise KBError("search_query_required")
    versions = {}
    for spec in args.version:
        if "=" not in spec:
            raise KBError("version_filter_requires_component_equals_version")
        key, value = spec.split("=", 1)
        if not key or not value:
            raise KBError("version_filter_requires_component_equals_version")
        versions[key] = value
    hits = []
    for record in records.values():
        if args.kind and record["kind"] != args.kind:
            continue
        if not args.include_deprecated and record["status"] == "deprecated":
            continue
        scope = record["applies_to"]
        if args.model and args.model.casefold() not in [x.casefold() for x in scope["models"]]:
            continue
        if args.hardware and args.hardware.casefold() not in [x.casefold() for x in scope["hardware"]]:
            continue
        if any(scope["software_versions"].get(k) != v for k, v in versions.items()):
            continue
        weighted = [(record["title"], 4), (" ".join(record["tags"]), 3),
                    (record["summary"], 2), (canonical(record["body"]), 1)]
        score = sum(weight for term in query_terms for text, weight in weighted if term in text.casefold())
        if score:
            hits.append({**{k: record[k] for k in ("id", "kind", "title", "summary", "status", "applies_to", "updated_at")},
                         "score": score, "revision": digest(record),
                         "path": record["kind"] + "s/" + record["id"] + ".json",
                         "compatibility": "metadata_match_only_requires_review",
                         "evidence_notes": evidence_checks(record, records, codex_home, repository)})
    priority = {"verified": 3, "partial": 2, "candidate": 1, "unresolved": 0, "deprecated": -1}
    hits.sort(key=lambda x: (x["score"], priority[x["status"]], x["updated_at"], x["id"]), reverse=True)
    return hits[:args.top_k]


def main(argv=None):
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Explicit shared store or isolated test store")
    parser.add_argument("--repo", type=Path, help="Shared codex-skills checkout; also CODEX_SKILLS_REPO")
    sub = parser.add_subparsers(dest="command", required=True)
    find = sub.add_parser("search")
    find.add_argument("--query", required=True)
    find.add_argument("--top-k", type=int, choices=range(1, 21), default=5)
    find.add_argument("--kind", choices=STATUSES)
    find.add_argument("--model")
    find.add_argument("--hardware")
    find.add_argument("--version", action="append", default=[])
    find.add_argument("--include-deprecated", action="store_true")
    view = sub.add_parser("show")
    view.add_argument("id")
    save = sub.add_parser("put")
    save.add_argument("--file", type=Path, required=True)
    save.add_argument("--expected-revision")
    sub.add_parser("locate")
    sub.add_parser("rebuild")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    try:
        root, repository = resolve_store(args, codex_home)
        if args.command == "put":
            result = put(root, load_json(args.file), args.expected_revision)
        elif args.command == "rebuild":
            with writer_lock(root):
                records = read_records(root)
                atomic_json(root / "index.json", index_for(records))
            result = {"state": "rebuilt", "records": len(records)}
        else:
            records = read_records(root)
            state = index_state(root, records)
            if args.command == "locate":
                result = {"backend": "repository_checkout" if repository else "explicit_store",
                          "knowledge_path": "knowledge-base/vllm-performance" if repository else "<EXPLICIT_ROOT>",
                          "git_checkout": bool(repository and (repository / ".git").exists()),
                          "records": len(records), "index_state": state,
                          "synchronization": "git_pull_push_required" if repository else "caller_managed"}
            elif args.command == "search":
                result = {"index_state": state, "results": search(records, args, codex_home, repository)}
            elif args.command == "show":
                if not valid_id(args.id) or args.id not in records:
                    raise KBError("record_not_found")
                record = records[args.id]
                result = {"revision": digest(record), "record": record,
                          "evidence_notes": evidence_checks(record, records, codex_home, repository)}
            else:
                warnings = {key: evidence_checks(value, records, codex_home, repository) for key, value in records.items()}
                result = {"records": len(records), "index_state": state,
                          "evidence_notes": {k: v for k, v in warnings.items() if v},
                          "privacy_screen": "passed_patterns_only_manual_review_still_required"}
                blocking = {"missing_case_evidence", "case_evidence_changed", "missing_related_record",
                            "skill_evidence_unavailable", "skill_evidence_changed", "source_case_deprecated",
                            "artifact_evidence_unavailable", "artifact_evidence_changed"}
                active_errors = any(blocking.intersection(notes) for key, notes in warnings.items()
                                    if records[key]["status"] != "deprecated")
                if state != "current" or active_errors:
                    print(canonical(result))
                    return 1
        print(canonical(result))
        return 0
    except KBError as error:
        print(canonical({"error": str(error)}))
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print(canonical({"error": "invalid_local_data_or_file_access"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
