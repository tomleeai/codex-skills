#!/usr/bin/env python3
"""Generate container-local rootinfo using a reviewed tool's single-host path."""

import argparse
import glob
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid


SUPPORTED_IDS = list(range(8))
TOOL_VERSION = "0.0.11"


def topology_ports(path, expected_ids):
    if expected_ids != SUPPORTED_IDS:
        raise ValueError("This adapter supports only the verified single-host physical IDs 0..7")
    path = Path(path).resolve(strict=True)
    if path.name != "atlas_850_1.json":
        raise ValueError("This adapter requires the verified atlas_850_1.json topology")
    topo = json.loads(path.read_text())
    ports = {i: set() for i in expected_ids}
    neighbors = {i: set() for i in expected_ids}
    for edge in topo["edge_list"]:
        if edge["net_layer"] != 0 or edge["link_type"] != "PEER2PEER":
            continue
        a, b = int(edge["local_a"]), int(edge["local_b"])
        if a not in ports or b not in ports or a == b:
            raise ValueError("Topology contains non-local or self-linked level-0 P2P IDs")
        ports[a].update(edge["local_a_ports"])
        ports[b].update(edge["local_b_ports"])
        neighbors[a].add(b)
        neighbors[b].add(a)
    for i in expected_ids:
        if neighbors[i] != set(expected_ids) - {i}:
            raise ValueError(f"Incomplete eight-device P2P topology at local_id {i}")
        if ports[i] != {f"1/{p}" for p in range(1, 8)}:
            raise ValueError(f"Unexpected die/port topology at local_id {i}: {ports[i]}")
    return path, ports


def validate_document(doc, topology, expected_ids, live_eids=None):
    topo_path, required_ports = topology_ports(topology, expected_ids)
    if doc.get("version") != "2.0" or doc.get("rank_count") != len(expected_ids):
        raise ValueError("Unexpected rootinfo version or rank_count")
    if Path(doc["topo_file_path"]).resolve(strict=True) != topo_path:
        raise ValueError("Rootinfo refers to a different topology")
    ranks = doc["rank_list"]
    if len(ranks) != len(expected_ids):
        raise ValueError("rank_list length does not match rank_count")
    ids = [r["device_id"] for r in ranks]
    if ids != expected_ids:
        raise ValueError(f"Unexpected physical device mapping: {ids}")
    instances = set()
    for rank in ranks:
        physical = rank["device_id"]
        local = rank["local_id"]
        if type(local) is not int or local != physical:
            raise ValueError(f"Invalid single-host local_id for device {physical}: {local}")
        levels = rank["level_list"]
        if len(levels) != 1 or levels[0]["net_layer"] != 0:
            raise ValueError("Expected only the single-host level-0 communication layer")
        level = levels[0]
        if level["net_type"] != "TOPO_FILE_DESC" or not level["net_instance_id"]:
            raise ValueError("Missing topology descriptor or communication-domain label")
        instances.add(level["net_instance_id"])
        addresses = level["rank_addr_list"]
        ports, eids = [], []
        for addr in addresses:
            eid = addr["addr"]
            if addr["addr_type"] != "EID" or not re.fullmatch(r"[0-9a-fA-F]{32}", eid):
                raise ValueError("Invalid EID address")
            if int(eid, 16) == 0 or addr["plane_id"] != "plane_1":
                raise ValueError("Invalid EID value or plane")
            if len(addr["ports"]) != 1:
                raise ValueError("Expected a single physical port per single-host EID")
            if live_eids is not None:
                if eid not in live_eids[physical]:
                    raise ValueError(f"EID for device {physical} was not returned by the live inventory")
                die, port = live_eids[physical][eid]
                if addr["ports"] != [f"{die}/{port}"]:
                    raise ValueError(f"EID/port mismatch for device {physical}")
            ports.extend(addr["ports"])
            eids.append(eid)
        if len(addresses) != 7 or len(set(eids)) != 7:
            raise ValueError(f"Expected seven distinct EIDs for device {physical}")
        if len(ports) != 7 or set(ports) != required_ports[local]:
            raise ValueError(f"Rootinfo port set disagrees with topology for device {physical}")
    if len(instances) != 1:
        raise ValueError("Single-host devices must belong to one level-0 communication domain")
    return {
        "status": "VALIDATED", "version": doc["version"],
        "rank_count": len(ranks), "physical_ids": ids,
        "local_ids": [r["local_id"] for r in ranks],
        "topology": str(topo_path), "topology_sha256": hashlib.sha256(topo_path.read_bytes()).hexdigest(),
        "live_eids_checked": live_eids is not None,
        "boundary": "Resource description validated; HCCL and weight loading require service evidence",
    }


def is_mountpoint(path):
    target = os.path.abspath(path)
    with open("/proc/self/mountinfo") as stream:
        for line in stream:
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
            if os.path.abspath(point) == target:
                return True
    return False


def install_document(path, data, replace=False):
    path = Path(path).absolute()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Refusing to overwrite a symlink, directory, or non-regular output")
    if is_mountpoint(path):
        raise ValueError("Output is a mount point; generate a container-local candidate and review its host owner")
    backup = None
    if path.exists():
        if not replace:
            raise ValueError("Output exists; inspect it first and use --replace to back it up")
        backup = str(path) + ".backup-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        shutil.copy2(path, backup)
    fd, temp = tempfile.mkstemp(prefix=".hccl-rootinfo-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return backup


def generate(args):
    if sys.platform != "linux":
        raise ValueError("Run this command inside the target Linux container")
    topology, _ = topology_ports(args.topology, args.expected_ids)
    if importlib.metadata.version("unofficial-ascend-tools") != TOOL_VERSION:
        raise ValueError(f"Reviewed adapter requires unofficial-ascend-tools=={TOOL_VERSION}")
    from unofficial_ascend_tools.rankinfo import rootinfo, product_server
    from unofficial_ascend_tools.rankinfo.config import ConfigMgr
    from unofficial_ascend_tools.rankinfo.dcmi import dcmi
    from unofficial_ascend_tools.rankinfo.npu import NPU
    from unofficial_ascend_tools.rankinfo.topo import TopoSingleFactory
    from unofficial_ascend_tools.rankinfo.urma_device import UrmaDevice

    dcmi.dcmi_init()
    mainboard = dcmi.get_mainboard_id()
    if mainboard != 39:
        raise ValueError(f"Unverified mainboard ID: {mainboard}; expected 39 (0x27)")
    visible = sorted(int(re.search(r"\d+$", p)[0]) for p in glob.glob("/dev/davinci[0-9]*"))
    if visible != args.expected_ids:
        raise ValueError(f"Unexpected container-visible device nodes: {visible}")
    physical = [dcmi.get_phyid_from_logicid(i) for i in range(len(visible))]
    if physical != args.expected_ids:
        raise ValueError(f"DCMI logical-to-physical mapping differs: {physical}")
    spod = dcmi.get_super_pod_info()
    settings = {
        "mainboard_id": mainboard, "server_index": spod.server_index,
        "super_pod_id": spod.super_pod_id, "super_pod_type": ord(spod.super_pod_type),
        "chassis_id": spod.chassis_id, "NPU_NUM": len(visible),
        "max_level": 1, "topo_path": str(topology),
    }
    for key, value in settings.items():
        ConfigMgr().set(key, value)
    TopoSingleFactory.set_topo_path(str(topology))
    info = rootinfo.RootInfo([])
    info.topo_file_path = str(topology)
    live_eids = {}
    for logical, phy in enumerate(physical):
        npu = NPU(logical, phy)
        npu.urma_dev_list = product_server.get_urma_device_list(logical, "server")
        live_eids[phy] = {
            eid: (UrmaDevice.get_die_id_by_eid(eid), UrmaDevice.get_phy_port_by_eid(eid))
            for dev in npu.urma_dev_list for eid in dev.eid_list
        }
        product_server.process_npu_for_server2(npu, mainboard)
        info.rank_list.append(npu)
    data = (json.dumps(info, indent=2, cls=rootinfo.RootInfoEncoder) + "\n").encode()
    result = validate_document(json.loads(data), topology, args.expected_ids, live_eids)
    result.update({"mainboard_id": mainboard, "server_index_observed": spod.server_index,
                   "tool_distribution": "unofficial-ascend-tools", "tool_version": TOOL_VERSION})
    result["backup"] = install_document(args.output, data, args.replace)
    result["output"] = str(Path(args.output).absolute())
    result["sha256"] = hashlib.sha256(data).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "validate"):
        sub = subs.add_parser(name)
        sub.add_argument("--topology", required=True)
        sub.add_argument("--expected-ids", type=int, nargs="+", required=True)
        sub.add_argument("--report")
        if name == "generate":
            sub.add_argument("--output", default="/etc/hccl_rootinfo.json")
            sub.add_argument("--replace", action="store_true")
        else:
            sub.add_argument("--path", default="/etc/hccl_rootinfo.json")
    args = parser.parse_args()
    if args.report and Path(args.report).absolute() == Path(getattr(args, "output", getattr(args, "path", ""))).absolute():
        parser.error("Report path must differ from the rootinfo path")
    report_stream = None
    try:
        if args.report:
            # Reserve evidence output before changing rootinfo; existing evidence is preserved.
            report_stream = Path(args.report).open("x", encoding="utf-8")
        if args.command == "generate":
            result = generate(args)
        else:
            path = Path(args.path)
            result = validate_document(json.loads(path.read_bytes()), args.topology, args.expected_ids)
            result.update({"output": str(path.absolute()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        rendered = json.dumps(result, indent=2) + "\n"
        if report_stream:
            report_stream.write(rendered)
        print(rendered, end="")
    except Exception as exc:
        failure = json.dumps({"status": "FAILED", "error": str(exc)})
        if report_stream:
            report_stream.write(failure + "\n")
        print(failure, file=sys.stderr)
        return 1
    finally:
        if report_stream:
            report_stream.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
