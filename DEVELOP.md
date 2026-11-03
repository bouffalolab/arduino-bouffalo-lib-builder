# Developing arduino-bl-lib-builder

This guide is for maintainers building the runtime libraries and host tools consumed by arduino-bl. Ordinary Arduino users install exported packages; they do not need this build environment.

## Architecture and ownership

The builder locks SDK and toolchain inputs, applies verified patches in a derived SDK tree, builds a selected profile, and exports libraries, headers, flags, linker resources, boot resources, and metadata as one versioned unit.

Arduino APIs belong in the [platform](https://github.com/bouffalolab/arduino-bouffalo/tree/master). AT/HCI contracts, RA4M1 control, and CMSIS-DAP belong in the [bridge application](https://github.com/bouffalolab/arduino-bouffalo-uno-r4-wifi-usb-bridge). Do not move SDK builds into ordinary sketch recipes.

## Prerequisites

| Purpose | Requirements |
| --- | --- |
| Maintainer host | Linux x86_64; Ubuntu 22.04 is the firmware CI baseline |
| Native build | Git, Bash, curl, make, tar with xz support, SHA-256 utilities, and native C/C++ tools |
| Dependency sources | Network access for the locked SDK, toolchain, and Arduino built-in tools |
| Firmware integration | Arduino CLI 1.5.1 |
| Frozen layout tool | Python 3.9.17, PyInstaller 6.15.0, and tomli 2.2.1 |
| Integration dependencies | cryptography 44.0.3, matching firmware CI |
| Isolated installation checks | bubblewrap (`bwrap`) |

Use a virtual environment. Source CI runs under Python 3.11; full firmware CI uses Python 3.9.17 because the layout-tool freezer enforces that exact version.

## Local environment

Clone the three master branches into the layout expected by the local tooling:

```sh
git clone --branch master https://github.com/bouffalolab/arduino-bouffalo.git arduino-bl
git clone --branch master https://github.com/bouffalolab/arduino-bouffalo-lib-builder.git arduino-bl-lib-builder
git clone --branch master https://github.com/bouffalolab/arduino-bouffalo-uno-r4-wifi-usb-bridge.git uno-r4-wifi-usb-bridge-bl
cd arduino-bl-lib-builder
python3.9 -m venv ../builder-venv
. ../builder-venv/bin/activate
python3 --version
python3 -m pip install -r tools/layout-tool-requirements.txt cryptography==44.0.3
```

Verify that `python3 --version` prints 3.9.17 before freezing the layout tool. The builder downloads the Xuantie toolchain pinned in `sources.json`; a separately installed RISC-V compiler is not a substitute.

For a release or reproduction, use the exact three commit SHAs selected by the candidate or workflow. The latest master tips are a development starting point, not an automatically validated combination.

## Build workflows

### Build a dependency profile

From the builder repository:

```sh
./build.sh bl616cl/default
```

The first run fetches the locked SDK and toolchain. The build operates in a derived SDK tree; the source checkout must remain clean, including ignored files.

The default working directory is `work/`. To use a separate location, set an absolute path before invoking any builder commands:

```sh
export BL_BUILDER_WORK="/absolute/path/to/builder-work"
./build.sh bl616cl/default
```

The selected profile's generated build directory is recreated on each build. Keep source changes in `profiles/` or versioned patches rather than generated SDK/build trees.

### Build packages and representative firmware

For the complete path, run from a clean checkout of all three repositories:

```sh
BL_BUILDER_WORK="$(mktemp -d /tmp/arduino-bl-build.XXXXXX)" \
  tools/ci/build_firmware.sh ../arduino-bl ../uno-r4-wifi-usb-bridge-bl
```

Use a fresh work directory for each full run: the current entry point creates its own Arduino sketchbook symlink and does not reuse an existing one. Do not discard local changes to satisfy its clean-tree requirement; validate a committed revision in separate clean checkouts.

This command builds the profile, exports runtime and flash-tool packages, and compiles representative serial, Wi-Fi, BLE Controller, filesystem, preferences, and bridge sketches. It records the source identities, logs, package manifests, image checks, and firmware hashes under the selected working directory.

For package-only iteration, use `tools/export_package.py` and `tools/export_flash_tools.py`. Their argument sets are shown in `tools/ci/build_firmware.sh`; use `--help` for details. Keep libraries, headers, configuration, flags, linker resources, and tool identities together.

## Directory structure

| Location | Responsibility |
| --- | --- |
| `sources.json` | SDK/toolchain revisions, URLs, hashes, and ordered patches |
| `profiles/` | Chip/profile configuration and SDK build carrier |
| `patches/` | Source patches with rationale, upstream status, and removal conditions |
| `tools/` | Package export, layout tooling, validation, and release tools |
| `tools/ci/` | Source-lock verification and integrated firmware build entry points |
| `docs/` | Controlled release procedures |
| `.github/workflows/` | Source CI and reusable firmware CI |
| `work/` | Default generated downloads, SDK trees, packages, logs, and firmware |

Generated files remain outside versioned sources. An export manifest records source identity and package contents; a version string alone does not establish compatibility.

## Testing

### Source checks and unit tests

```sh
tools/ci/run_source_ci.sh
git diff --check
```

The source entry point validates source locks and patch hashes, runs host unit/layout tests, and checks Python and shell syntax. If the sibling platform and bridge are present, it also checks their relevant test-source syntax. Alternate locations can be supplied through `ARDUINO_BL_PLATFORM` and `ARDUINO_BL_BRIDGE`.

Host release tests use fixture archives and simulated API responses. They test package/release behavior without proving production publication or board functionality.

### Package installation and isolation

After producing the default `work/out/` packages, run:

```sh
tools/install_check.sh ../arduino-bl work/bouffalo_sdk work/build
```

If `BL_BUILDER_WORK` is set, replace the last two arguments with the corresponding absolute SDK and build directories in that work tree.

The check serves a local package index, installs into an empty Arduino CLI environment, then compiles with network disabled and SDK/build paths hidden. It also checks relocation, read-only installed packages, and a sketchbook Core override.

It archives the platform's committed HEAD, so this is a package acceptance check rather than a test of uncommitted platform edits. Initial installation requires network access for Arduino built-in tools.

### Hardware acceptance

Source checks, export validation, installation, and firmware builds are software evidence. They do not count as hardware passes.

A functional acceptance record must identify the exact board/module, wiring, package and firmware hashes, profile/layout, device, configuration, expected behavior, and raw logs. Record flashing, serial execution, and end-to-end results separately. Firmware CI performs no hardware callback.

## Development and contribution workflow

1. Identify whether the change belongs to SDK inputs, a profile, a patch, or package tooling.
2. Lock all source and toolchain updates explicitly. Add the patch rationale, upstream status, checksum, and removal condition when needed.
3. Build/export the affected profile and verify the package's source identity, contents, and path independence.
4. Run source, integration, and installation checks as appropriate; obtain hardware evidence for affected behavior.
5. Coordinate platform/bridge compatibility and submit a PR with the change rationale, package impact, and validation limits.

Keep SDK checkout modifications out of the build; only the derived tree receives patches. Avoid host-specific fallback paths in exported packages. Use English comments/docstrings and preserve component licenses.

## Release workflow

A release freezes exact source revisions, packages, checksums, license notices, hardware scope, and compatibility metadata. Review that candidate before publication and preserve its bytes after approval.

Use the [controlled release guide](docs/releasing.md) for candidate freezing, isolated rehearsal, approval, publication, and index rollback. Rollback of a package index does not imply firmware or persistent-data downgrade compatibility. CI produces evidence; publication remains a separate authorized maintainer action.
