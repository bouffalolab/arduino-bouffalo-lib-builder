# arduino-bl-lib-builder

[![Source CI](https://github.com/bouffalolab/arduino-bouffalo-lib-builder/actions/workflows/source-ci.yml/badge.svg?branch=master)](https://github.com/bouffalolab/arduino-bouffalo-lib-builder/actions/workflows/source-ci.yml)
[![Firmware CI](https://github.com/bouffalolab/arduino-bouffalo-lib-builder/actions/workflows/firmware-ci.yml/badge.svg?branch=master)](https://github.com/bouffalolab/arduino-bouffalo-lib-builder/actions/workflows/firmware-ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

The maintainer-side build system for the versioned dependency packages consumed by [arduino-bl](https://github.com/bouffalolab/arduino-bouffalo).

## Features

- Locks SDK, toolchain, source revisions, and checksums for repeatable builds.
- Applies reviewed compatibility changes in an isolated build tree.
- Builds and exports BL616CL runtime libraries for FreeRTOS, Wi-Fi, networking, USB, storage, and optional BLE Controller use.
- Produces the flash and image tools required by the Arduino platform.
- Removes build-host paths from exported packages and records package metadata for consumers.
- Integrates platform and UNO R4 WiFi bridge builds in continuous integration.

This repository is intended for package maintainers and release engineering. A normal Arduino sketch build consumes an exported package and does not run this builder or require the full SDK source tree.

## Getting started

### Prerequisites

- Linux x86_64 for the currently supported package and flash-tool workflow.
- Git, Python, a C/C++ build environment, and network access for pinned source downloads.
- The source revisions and toolchain selected by the repository's release configuration.

### Build a package

1. Check out the `master` branch and review the selected source and toolchain revisions.
2. Prepare the pinned SDK and toolchain in the maintainer workspace.
3. Select the target chip and build profile.
4. Export the runtime and flash-tool packages with a release version.
5. Run the package, source, and integration checks before publishing the package index.

The platform, dependency packages, and bridge firmware must be released as a compatible set. Do not mix packages from unrelated source revisions.

## Usage

The exported packages are consumed by the Arduino platform as follows:

| Package | Purpose |
| --- | --- |
| Runtime libraries | Chip initialization, RTOS, Wi-Fi, networking, USB, storage, and optional BLE Controller support |
| Flash tools | Image preparation, layout handling, and firmware upload support |
| Package metadata | Versions, checksums, source identity, and compatibility information |

Maintainers should keep the source lock, patch rationale, package metadata, and validation results together for each release.

## Directory structure

- **Source configuration** — pinned SDK and toolchain identities.
- **Profiles** — chip and feature selections for dependency builds.
- **Patches** — reviewed compatibility changes with their removal conditions.
- **Export and validation** — package generation, metadata, smoke checks, and release checks.
- **CI and documentation** — source locks, integrated builds, and maintainer guidance.

## Contributing

See the [developer guide](DEVELOP.md) for prerequisites, local setup, package builds, validation, and the release workflow.

Open GitHub Issues for reproducibility problems, missing package capabilities, or release-tool defects. Pull requests should explain the affected package, source or configuration change, compatibility impact, and validation performed. Source or patch updates must include refreshed checksums and a clear upstream status.

## License and acknowledgement

This repository is released under the [Apache License 2.0](LICENSE). Generated packages may contain SDK, toolchain, and other third-party components under their own licenses; the corresponding notices and source identities must remain available with each release.

The builder supplies packages for [arduino-bl](https://github.com/bouffalolab/arduino-bouffalo) and the [UNO R4 WiFi USB bridge](https://github.com/bouffalolab/arduino-bouffalo-uno-r4-wifi-usb-bridge).
