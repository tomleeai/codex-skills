#!/usr/bin/env python3
"""Prepare an annotated launch copy without executing or patching any dependency."""
import argparse
import base64
import datetime
import difflib
import hashlib
import json
import re
import shlex
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(source, run_dir, case_id, anchor):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", case_id):
        raise ValueError("case-id must be a lowercase slug")
    if not anchor or any(c in anchor for c in "\r\n\x00"):
        raise ValueError("after-line must be one nonempty exact line")
    source = source.resolve(strict=True)
    run_dir = run_dir.resolve()
    if run_dir.exists():
        raise ValueError("run-dir already exists; use a new run directory")
    raw = source.read_bytes()
    original = raw.decode("utf-8")
    if "\x00" in original or "OPERATOR-FIX" in original:
        raise ValueError("source contains NUL or an existing repair marker; review manually")
    lines = original.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == anchor]
    if len(matches) != 1:
        raise ValueError("after-line must match exactly once")
    index = matches[0]
    if not lines[index].endswith("\n"):
        raise ValueError("anchor must end with a newline")
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    overlay = (run_dir / "overlays" / "python").as_posix()
    if any(c in overlay for c in "\r\n\x00"):
        raise ValueError("invalid overlay path")
    block = newline.join([
        f"# BEGIN OPERATOR-FIX [{case_id}]",
        "# Load the isolated operator repair; see patches/ and reports/REPORT.md.",
        "# Rollback: stop this instance and use the original launch entry.",
        f"export PYTHONPATH={shlex.quote(overlay)}${{PYTHONPATH:+:$PYTHONPATH}}",
        f"# END OPERATOR-FIX [{case_id}]",
        "",
    ])
    changed = "".join(lines[:index + 1]) + block + "".join(lines[index + 1:])
    fixed = changed.encode("utf-8")
    run_dir.mkdir(parents=True, exist_ok=False)
    for directory in ["originals", "launch", "overlays/python", "patches", "probes",
                      "logs/00-baseline", "results", "reports"]:
        (run_dir / directory).mkdir(parents=True, exist_ok=True)
    snapshot = Path("originals") / source.name
    launch = Path("launch") / (source.stem + ".fixed" + source.suffix)
    (run_dir / snapshot).write_bytes(raw)
    (run_dir / launch).write_bytes(fixed)
    diff = "".join(difflib.unified_diff(original.splitlines(keepends=True),
                    changed.splitlines(keepends=True), fromfile=snapshot.as_posix(),
                    tofile=launch.as_posix()))
    (run_dir / "patches/launch-overlay.patch").write_bytes(diff.encode("utf-8"))
    manifest = {
        "schema_version": 1, "case_id": case_id,
        "prepared_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "PREPARED_NOT_EXECUTED", "source_launch": str(source),
        "source_sha256": digest(raw), "original_snapshot": snapshot.as_posix(),
        "launch_copy": launch.as_posix(), "launch_sha256": digest(fixed),
        "overlay": "overlays/python", "after_line": anchor,
        "inserted_block_base64": base64.b64encode(block.encode("utf-8")).decode("ascii"),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                                           + "\n", encoding="utf-8")
    verify(run_dir)
    print(f"Prepared: {run_dir}; dependency repair and runtime validation are still required.")


def contained(run_dir, relative):
    target = (run_dir / relative).resolve(strict=True)
    if not target.is_relative_to(run_dir):
        raise ValueError("manifest artifact escapes run-dir")
    return target


def verify(run_dir):
    run_dir = run_dir.resolve(strict=True)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    raw = contained(run_dir, manifest["original_snapshot"]).read_bytes()
    fixed = contained(run_dir, manifest["launch_copy"]).read_bytes()
    block = base64.b64decode(manifest["inserted_block_base64"], validate=True)
    if digest(raw) != manifest["source_sha256"]:
        raise ValueError("original snapshot hash changed")
    if digest(Path(manifest["source_launch"]).read_bytes()) != digest(raw):
        raise ValueError("source launch changed since snapshot; review drift")
    if digest(fixed) != manifest["launch_sha256"]:
        raise ValueError("launch copy hash changed; review diff")
    if fixed.count(block) != 1 or fixed.replace(block, b"", 1) != raw:
        raise ValueError("launch copy differs beyond the recorded repair block")
    if not contained(run_dir, manifest["overlay"]).is_dir():
        raise ValueError("overlay directory missing")
    print("Launch text verified; package loading, shell semantics and service behavior NOT validated.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="verify an existing run directory")
    parser.add_argument("--source-launch", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--after-line", help="unique literal line after environment initialization")
    args = parser.parse_args()
    creation = [args.source_launch, args.run_dir, args.case_id, args.after_line]
    try:
        if args.verify:
            if any(item is not None for item in creation):
                parser.error("--verify cannot be combined with preparation options")
            verify(args.verify)
        else:
            if any(item is None for item in creation):
                parser.error("preparation requires all four options")
            prepare(*creation)
    except (ValueError, OSError, KeyError, UnicodeError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
