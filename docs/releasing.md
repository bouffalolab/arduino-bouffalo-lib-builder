# Controlled releases

The initial CI builds firmware; it does not publish a version. Publishing is a
separate maintainer action. Source repositories are public GitHub repositories
under `bouffalolab`. The release tool targets a static HTTP directory, allowing an
isolated rehearsal before a production download service is selected and enabled.
No production channel, version tag, or public package index is created by this tool.
ARDUINO-56 retains final public release approval and the production URL decision.

Use Linux x86_64 and the validated BL616CL UNO R4 WiFi 1V0 combination. Do not
advertise other hosts or boards based on a successful build. Firmware CI evidence
and existing hardware acceptance must remain distinct.

## Freeze a candidate

Create a new candidate directory containing:

- `package_bouffalolab_index.json`, produced by `tools/make_index.py` for the exact
  checked-out source revision.
- Every archive referenced by that index, including the pinned toolchain archive.
- `provenance.json` recording `commits.platform`, `commits.builder`, and
  `commits.bridge` as exact 40-character SHAs; `sdk` with its commit and patch hashes;
  `compatibility` with package/profile/host/board identities; license evidence,
  hardware acceptance references and the originating workflow ID when applicable.

Do not rebuild after approving this directory. In particular, do not replace an
already reviewed archive with a new build that happens to have the same version.
Preserve firmware hashes separately from source hashes when their identities differ.

```sh
python3 tools/release.py freeze --candidate /absolute/path/to/candidate
```

The printed SHA-256 identifies `candidate.json`, which locks the index, provenance,
and archive sizes and hashes. Keep the complete directory as the immutable input.

## Approve and promote in isolation

Create an approval JSON outside the source checkout. Its `candidate_sha256` must
match the printed identity. `approved_by` identifies the maintainer, and each
acceptance value points to actual evidence for that exact combination:

```json
{
  "candidate_sha256": "the complete hash printed by freeze",
  "approved_by": "maintainer approving the isolated rehearsal",
  "channel": "isolated",
  "license_approved": true,
  "acceptance": {
    "ARDUINO-52": "recovery acceptance reference",
    "ARDUINO-53": "integration acceptance reference",
    "ARDUINO-54": "license scope acceptance reference",
    "ARDUINO-55": "exact package acceptance reference",
    "ARDUINO-57": "phone demo acceptance reference",
    "ARDUINO-58": "IDE acceptance reference",
    "ARDUINO-59": "architecture review reference"
  }
}
```

The tool checks presence and binding; the maintainer must review the truth and
applicability of those references. A JSON file is a review record, not an identity
provider or a substitute for user approval. Keep credentials out of both files.

Start an actual HTTP server for the empty target directory, then promote:

```sh
python3 -m http.server 8978 --bind 127.0.0.1 --directory /absolute/path/to/target
python3 tools/release.py promote \
  --candidate /absolute/path/to/candidate --approval /absolute/path/to/approval.json \
  --target /absolute/path/to/target --base-url http://127.0.0.1:8978
```

Run the server in a separate terminal. The approval with `channel=isolated` permits
only loopback URLs. Promotion validates the frozen input, preserves existing
versions, copies missing assets without overwriting different bytes, GETs and hashes
the actual downloads, and only then atomically replaces the index. A failed download
leaves the current index intact; already copied immutable assets may remain for a
retry. Repeating the exact promotion is safe. Use one publishing process at a time.

For a production static host, the target directory must be that host's served
directory and the base URL must resolve to the uploaded files. A GitHub Release or
object-store API requires a separate host adapter; this initial tool does not claim
to deploy those services. An unapproved production adapter is outside this rehearsal.
Public use requires `channel=stable` and an explicit `ARDUINO-56` approval reference,
in addition to the other gates and the actual user's authorization.

## Install and recover

In an isolated Arduino CLI configuration, add the served index URL, update indexes,
then install the explicit package version:

```sh
arduino-cli --config-file /absolute/path/to/isolated.yaml core update-index \
  --additional-urls http://127.0.0.1:8978/package_bouffalolab_index.json
arduino-cli --config-file /absolute/path/to/isolated.yaml core install bouffalolab:bl@0.1.0 \
  --additional-urls http://127.0.0.1:8978/package_bouffalolab_index.json
```

Compilation and installation are host checks. Apply hardware acceptance only to its
recorded board, archive, firmware, partition layout and test configuration.

Each index replacement preserves the prior bytes as `history/<sha256>.json`. To
restore that entry point, retain all old assets and verify them before replacement:

```sh
python3 tools/release.py restore --snapshot /absolute/path/to/target/history/HASH.json \
  --target /absolute/path/to/target
```

Then update Arduino indexes and explicitly install the previous version. Restoring
an index does not uninstall a locally installed version or downgrade device firmware.
Never delete old archives to withdraw a version from the latest index. Data-preserving
firmware rollback requires the same compatible partition geometry and persistent-data
format; do not infer arbitrary downgrade compatibility from this index operation.
Do not format LittleFS, erase all flash, or alter persistent storage during rollback.

`tools/test_release.py` uses fixture archives and host HTTP; these are software
checks, not firmware or board acceptance. Full package rehearsals must additionally
use real archives and an empty Arduino CLI environment. Keep failure logs and the
exact prior index so a retry or rollback remains reviewable.
