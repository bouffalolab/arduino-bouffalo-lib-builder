#!/usr/bin/env python3
"""Freeze exact archives, promote an approved index, or restore a verified snapshot.

Promotion never rebuilds sources. The target is a maintainer-owned static HTTP
directory. Public assets and index require separate ARDUINO-56 approval.
"""

import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

INDEX = "package_bouffalolab_index.json"
GATES = ("ARDUINO-52", "ARDUINO-53", "ARDUINO-54", "ARDUINO-55",
         "ARDUINO-57", "ARDUINO-58", "ARDUINO-59")


def digest(path):
    """Hash a file without loading an archive into memory."""
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    """Read one JSON document."""
    return json.loads(Path(path).read_text())


def save(path, value):
    """Write one JSON document with stable formatting."""
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def archives(index):
    """Iterate Arduino platform and host-tool download descriptors."""
    for package in index["packages"]:
        yield from package["platforms"]
        for tool in package["tools"]:
            yield from tool["systems"]


def freeze(candidate):
    """Lock index, provenance and every referenced local archive in a manifest."""
    candidate = Path(candidate)
    provenance = load(candidate / "provenance.json")
    for name in ("platform", "builder", "bridge"):
        commit = provenance["commits"][name]
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("expected exact source commit: " + name)
    if not provenance.get("compatibility") or not provenance.get("sdk"):
        raise ValueError("SDK/configuration and compatibility provenance required")
    index = load(candidate / INDEX)
    files = {INDEX, "provenance.json"}
    for entry in archives(index):
        name = entry["archiveFileName"]
        if Path(name).name != name:
            raise ValueError("archiveFileName must be a basename")
        path = candidate / name
        if entry["checksum"] != "SHA-256:" + digest(path) or int(entry["size"]) != path.stat().st_size:
            raise ValueError("archive identity mismatch: " + name)
        files.add(name)
    manifest = {"schema": "arduino-bl-release/v1", "files": {
        name: {"sha256": digest(candidate / name), "size": (candidate / name).stat().st_size}
        for name in sorted(files)}}
    save(candidate / "candidate.json", manifest)
    return digest(candidate / "candidate.json")


def verify_downloads(index):
    """GET and hash every real HTTP asset before allowing an index update."""
    for entry in archives(index):
        h = hashlib.sha256()
        size = 0
        with urlopen(entry["url"], timeout=120) as response:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                h.update(block)
        if entry["checksum"] != "SHA-256:" + h.hexdigest() or size != int(entry["size"]):
            raise ValueError("download identity mismatch: " + entry["url"])


def merge(previous, incoming):
    """Keep older versions and reject changes to an existing version's identity."""
    result = copy.deepcopy(previous)
    for new in incoming["packages"]:
        old = next((p for p in result["packages"] if p["name"] == new["name"]), None)
        if old is None:
            result["packages"].append(copy.deepcopy(new))
            continue
        for kind, keys in (("platforms", ("architecture", "version")), ("tools", ("name", "version"))):
            for entry in new[kind]:
                match = next((e for e in old[kind] if all(e[k] == entry[k] for k in keys)), None)
                if match is None:
                    old[kind].append(copy.deepcopy(entry))
                elif match != entry:
                    raise ValueError("existing version is immutable: " + str(tuple(entry[k] for k in keys)))
    for package in result["packages"]:
        for platform in package["platforms"]:
            for dependency in platform["toolsDependencies"]:
                owner = next(p for p in result["packages"] if p["name"] == dependency["packager"])
                if not any(t["name"] == dependency["name"] and t["version"] == dependency["version"]
                           for t in owner["tools"]):
                    raise ValueError("missing toolsDependency: " + str(dependency))
    return result


def replace_index(target, index):
    """Save the previous index by content hash, then atomically replace it."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    current = target / INDEX
    if current.exists():
        history = target / "history"
        history.mkdir(exist_ok=True)
        snapshot = history / (digest(current) + ".json")
        if not snapshot.exists():
            shutil.copy2(current, snapshot)
    pending = target / (INDEX + ".pending")
    save(pending, index)
    os.replace(pending, current)
    return digest(current)


def promote(candidate, approval_path, target, base_url):
    """Verify approval and candidate, expose immutable assets, then publish the index."""
    candidate, target = Path(candidate), Path(target)
    approval = load(approval_path)
    if approval.get("candidate_sha256") != digest(candidate / "candidate.json"):
        raise ValueError("approval does not identify this candidate")
    if not approval.get("approved_by") or not approval.get("license_approved"):
        raise ValueError("maintainer and license approval required")
    if any(not approval.get("acceptance", {}).get(gate) for gate in GATES):
        raise ValueError("acceptance evidence missing")
    if approval.get("channel") == "isolated":
        if urlparse(base_url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("isolated approval only permits loopback downloads")
    elif approval.get("channel") != "stable" or not approval.get("ARDUINO-56"):
        raise ValueError("stable promotion requires ARDUINO-56 approval")
    for name, identity in load(candidate / "candidate.json")["files"].items():
        path = candidate / name
        if digest(path) != identity["sha256"] or path.stat().st_size != identity["size"]:
            raise ValueError("frozen candidate changed: " + name)
    incoming = load(candidate / INDEX)
    for entry in archives(incoming):
        entry["url"] = base_url.rstrip("/") + "/" + entry["archiveFileName"]
    previous = load(target / INDEX) if (target / INDEX).exists() else {"packages": []}
    merged = merge(previous, incoming)
    target.mkdir(parents=True, exist_ok=True)
    for entry in archives(incoming):
        name = entry["archiveFileName"]
        dest = target / name
        if dest.exists():
            if digest(dest) != digest(candidate / name):
                raise ValueError("existing asset is immutable: " + name)
        else:
            shutil.copy2(candidate / name, dest)
    verify_downloads(merged)
    return replace_index(target, merged)


def restore(snapshot, target):
    """Restore a previous index only after its retained assets pass HTTP verification."""
    index = load(snapshot)
    verify_downloads(index)
    return replace_index(target, index)


def main():
    """Run a maintainer-requested freeze, promotion or index restoration."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("freeze")
    p.add_argument("--candidate", required=True)
    p = commands.add_parser("promote")
    for name in ("candidate", "approval", "target", "base-url"):
        p.add_argument("--" + name, required=True)
    p = commands.add_parser("restore")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        print(freeze(args.candidate))
    elif args.command == "promote":
        print(promote(args.candidate, args.approval, args.target, args.base_url))
    else:
        print(restore(args.snapshot, args.target))


if __name__ == "__main__":
    main()
