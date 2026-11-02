#!/usr/bin/env python3
"""Plan or execute an approved GitHub Release and master package-index update.

No remote writes occur without --execute and a stable ARDUINO-56 approval.
Authenticate gh and Git using the maintainer's existing credential mechanisms.
"""

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import release

REPOSITORY = "bouffalolab/arduino-bouffalo"


def command(*args):
    """Run a checked command, returning its stdout without inspecting credentials."""
    return subprocess.check_output(list(args), text=True)


def prepare(candidate):
    """Validate frozen local files and derive the immutable GitHub asset/index plan."""
    candidate = Path(candidate)
    manifest = release.load(candidate / "candidate.json")
    for name, identity in manifest["files"].items():
        if release.digest(candidate / name) != identity["sha256"]:
            raise ValueError("frozen candidate changed: " + name)
    index = release.load(candidate / release.INDEX)
    platforms = [p for package in index["packages"] for p in package["platforms"]]
    if len(platforms) != 1:
        raise ValueError("one approved platform version per release required")
    tag = "v" + platforms[0]["version"]
    base_url = "https://github.com/%s/releases/download/%s" % (REPOSITORY, tag)
    for entry in release.archives(index):
        entry["url"] = base_url + "/" + entry["archiveFileName"]
    provenance = release.load(candidate / "provenance.json")
    return {"repository": REPOSITORY, "tag": tag, "target": provenance["commits"]["platform"],
            "index_branch": "master", "base_url": base_url,
            "candidate_sha256": release.digest(candidate / "candidate.json"),
            "assets": sorted({e["archiveFileName"] for e in release.archives(index)}),
            "index": index}


def existing_index():
    """Read the actual master index; permit only an exact HTTP 404 as initial absence."""
    response = subprocess.run(["gh", "api", "repos/" + REPOSITORY + "/contents/" + release.INDEX,
                               "-f", "ref=master", "--method", "GET"], capture_output=True, text=True)
    if response.returncode:
        if "HTTP 404" in response.stderr:
            return {"packages": []}, None
        raise RuntimeError(response.stderr)
    data = json.loads(response.stdout)
    return json.loads(base64.b64decode(data["content"])), data["sha"]


def update_index(index, prior_sha, message):
    """Commit a verified index to master, using GitHub's prior-blob conflict check."""
    payload = {"message": message, "branch": "master",
               "content": base64.b64encode((json.dumps(index, indent=2) + "\n").encode()).decode()}
    if prior_sha:
        payload["sha"] = prior_sha
    # A file supplies JSON directly; shell interpolation never carries index contents.
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "update.json"
        release.save(path, payload)
        command("gh", "api", "--method", "PUT", "repos/" + REPOSITORY + "/contents/" + release.INDEX,
                "--input", str(path))


def execute(candidate, approval, plan):
    """Publish immutable assets first, verify HTTP, then update the master index."""
    candidate = Path(candidate)
    release.verify_candidate(candidate, approval, plan["base_url"])
    previous, prior_sha = existing_index()
    merged = release.merge(previous, plan["index"])
    tag = plan["tag"]
    # Tag target must be the approved platform commit, even when reusing an existing draft.
    tag_ref = subprocess.run(["git", "ls-remote", "git@github.com:" + REPOSITORY + ".git",
                              "refs/tags/" + tag, "refs/tags/" + tag + "^{}"],
                             capture_output=True, text=True, check=True)
    if tag_ref.stdout.strip():
        lines = tag_ref.stdout.strip().splitlines()
        target = next((line.split()[0] for line in lines if line.endswith("^{}")), lines[0].split()[0])
        if target != plan["target"]:
            raise ValueError("existing tag does not identify approved platform commit")
    releases = json.loads(command("gh", "api", "--paginate", "--slurp", "repos/" + REPOSITORY + "/releases"))
    found = next((r for page in releases for r in page if r["tag_name"] == tag), None)
    if found is None:
        command("gh", "release", "create", tag, "--repo", REPOSITORY, "--draft", "--target", plan["target"],
                "--title", tag, "--notes", "Approved candidate " + plan["candidate_sha256"])
        assets = set()
        draft = True
    else:
        if found["target_commitish"] != plan["target"] and not tag_ref.stdout.strip():
            raise ValueError("existing draft target differs from candidate")
        assets = {a["name"] for a in found["assets"]}
        draft = found["draft"]
    publish_assets = sorted({e["archiveFileName"] for e in release.archives(merged)
                             if e["url"].startswith(plan["base_url"] + "/")})
    for name in publish_assets:
        if name in assets:
            with tempfile.TemporaryDirectory() as temp:
                command("gh", "release", "download", tag, "--repo", REPOSITORY,
                        "--pattern", name, "--dir", temp)
                if release.digest(Path(temp) / name) != release.digest(candidate / name):
                    raise ValueError("existing release asset is immutable: " + name)
        else:
            command("gh", "release", "upload", tag, str(candidate / name), "--repo", REPOSITORY)
    if draft:
        command("gh", "release", "edit", tag, "--repo", REPOSITORY, "--draft=false")
    release.verify_downloads(merged)
    if merged != previous:
        maintainer = release.load(approval)["approved_by"]
        update_index(merged, prior_sha,
                     "chore(release): publish " + tag + " index\n\nJira-Id: ARDUINO-61\n\nSigned-off-by: " + maintainer)
    return {"tag": tag, "index_sha256_after_serialization": hashlib.sha256(
        (json.dumps(merged, indent=2) + "\n").encode()).hexdigest()}


def main():
    """Print a read-only release plan unless execution was explicitly requested."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--approval")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan = prepare(args.candidate)
    if args.execute:
        if not args.approval:
            parser.error("--execute requires --approval with stable ARDUINO-56 approval")
        print(json.dumps(execute(args.candidate, args.approval, plan), indent=2))
    else:
        print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
