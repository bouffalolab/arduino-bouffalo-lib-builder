# arduino-bl-lib-builder

Builds and releases versioned dependency packages for arduino-bl. Fetching, configuring, and patching the full SDK belongs exclusively to this maintainer workflow.

Current stage: builds and exports local BL616CL packages `bl616cl-libs` (profile `default`: FreeRTOS, lwIP, Wi-Fi STA/AP through wl80211, CherryUSB device CDC/HID, and optional external-host BLE UART HCI Controller) and `bl-flash-tools` for image post-processing and flashing. They have not been publicly released.

## Usage

```sh
./build.sh                          # Fetch the pinned SDK/toolchain and build profiles/bl616cl/default.
python3 tools/export_package.py \
  --build work/build/bl616cl-default --build-log work/build/bl616cl-default.log \
  --sdk work/bouffalo_sdk --sdk-tree work/build/bl616cl-default.sdk --sources sources.json \
  --toolchain-bin work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  --profile bl616cl/default --version 0.9.0 --out work/out/bl616cl-libs
tools/smoke_test.sh work/out/bl616cl-libs \
  work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  work/build/bl616cl-default/build/build_out/arduino_bl_profile_bl616cl.elf \
  work/bouffalo_sdk work/build      # Further arguments are paths to hide; the original package is hidden automatically.
python3 tools/test_export_package.py # Export unit tests; BL_TEST_GCC enables additional checks with real gcc.
python3 tools/test_prepare_sdk_tree.py
tools/build_layout_tool.sh work/layout-tool # Python 3.9.17, pinned requirements
python3 tools/export_flash_tools.py \
  --sdk work/bouffalo_sdk --sources sources.json --chip bl616cl \
  --version 0.2.0 --layout-tool work/layout-tool/dist/bl-layout \
  --out work/out/bl-flash-tools
python3 tools/test_export_flash_tools.py
tools/install_check.sh ../arduino-bl \
  work/bouffalo_sdk work/build      # Local package-index installation check; further arguments are paths to hide.
```

`sources.json` pins the sources and checksums of the SDK (public bouffalo_sdk master commit `1cd1fd51`, 3 commits after v2.3.37, including a binary compatibility fix for the wl80211 prebuilt library; without it, connecting to a WPA3 AP crashes; return to tag pinning once an official release includes the fix) and toolchain (Xuantie GCC 10.2.0 V2.6.1). `work/` holds checkouts, downloads, and build outputs and is not tracked. Builds run under `work/build/`. Before and after building, the SDK checkout must contain no changes or new files, including ignored files; otherwise `build.sh` fails. Export verifies that the checkout matches `sources.json`. `--out` replaces only an empty directory or a previous export.

Packages contain no build-host paths. The profile maps the work directory to `/work` and the SDK directory to `.` using `-ffile-prefix-map`; `build.sh` passes the work directory through `BL_PREFIX_MAP_ROOT`. Export converts SDK paths in `autoconfig.h` to relative paths and checks all exported files, including `.a` archives, failing on build-host paths. It also uses `strip -D --strip-debug` to remove library debug information (about 40 MB down to 27 MB, retaining symbols and LTO sections without changing the linked image).

## SDK patches

Builds do not use the checkout directly. `tools/prepare_sdk_tree.py` hard-links tracked checkout files into `work/build/<profile>.sdk`, then applies `sdk.patches` from `sources.json` in order after verifying sha256. Files changed by patches are first replaced with independent copies, leaving the checkout unchanged. SDK builds use git to generate version information; `build.sh` directs this to the original checkout. Patched libraries carry a dirty marker, such as lhal `1cd1fd51-dirty`; a shallow checkout without tags reports only the commit. Export uses `--sdk-tree` to select this tree and verifies its recorded commit and patches against `sources.json`. The package manifest records patches under `sdk`.

Patches live in `patches/sdk/` with paths relative to the SDK root (`-p1`). Their headers document the problem, evidence, upstream change, and removal conditions. Fixes go upstream first and reach public bouffalo_sdk releases after merging. Remove a patch when `sources.json` advances to a release containing it.

| Patch | Change | Upstream |
| --- | --- | --- |
| `lhal-usb-ep0-stall-cx-done.patch` | Set `USB_CX_STL` and `USB_CX_DONE` in the same register write when stalling USB device EP0; otherwise, after an OUT control request is rejected, EP0 stops responding until a bus reset | Gerrit lhal 11805 |

## Build profiles

`profiles/<chip>/<profile>/` is an SDK project:

- `defconfig` selects components. Wi-Fi uses wl80211 rather than FHOST. Components and Wi-Fi configuration, including `FreeRTOSConfig.h` and `lwipopts_user.h`, follow SDK `examples/wifi/macsw_bare`, excluding only SHELL and IPERF.
- mbedTLS is SDK version 3.6. The active configuration is `config-tls-generic.h` (`MBEDTLS_CONFIG_FILE` in `flags/defines`), with options from defconfig's `CONFIG_MBEDTLS_*`. The profile's inherited macsw_bare `mbedtls_sample_config.h` applies only to `CONFIG_MBEDTLS_V2` and is inactive here.
- TLS (ticket 0029) additionally enables P-384, P-521, X25519, and `bl_crt_bundle` in defconfig. During the build, SDK `gen_crt_bundle.py` converts `cacrt_all.pem` (147 certificates) into a bundle, compiled into `libapp.a` by `arduino_crt_bundle.S`. Symbols `arduino_crt_bundle` and `arduino_crt_bundle_size` are declared in exported `arduino_crt_bundle.h`. The bundle occupies about 66 KB and is linked only when used. Additional curves add about 14 KB of flash to Wi-Fi firmware because WPA3 SAE curve loading references all enabled curves; RAM usage is unchanged.
- `FreeRTOSConfig.h`, `lwipopts_user.h`, `usb_config.h`, and `mbedtls_sample_config.h` affect library layouts and are exported with the package. The platform must use these exact copies.
- Since 0.4.1, CherryUSB HID classes and headers support the platform's optional raw HID interface. Application registration determines HID enumeration; ordinary sketches still enumerate only CDC. CMSIS-DAP and RA4M1 pin control are excluded from the dependency package.
- Since 0.5.0, the package uses `libbtblecontroller_bl616cl_uarthci.a` and matching headers from the same pinned GitHub SDK. Original archive SHA-256: `307b72a88fd2a1a9875b283de959972a53851d0085079fa2c5d69c60e0128bab`; library string: 1.6.212. `CONFIG_BLE_HOST_DISABLE=y`. Upstream presets fix EM=32 KB, 2 connections, 4 ACL TX buffers, 7 activities, CIS/BIS enabled, and CTE disabled. These build options do not establish hardware feature acceptance. `manifest.controller` records the original archive and exported library/configuration entries; the stripped export archive hash is listed separately in `files`.
- The ABI matches the other packaged libraries: ELF32 little-endian RISC-V, `rv32imafc_xtheade`, `ilp32f`, `-fshort-enums`, Xuantie GCC 10.2.0 V2.6.1. The entire profile reserves 32 KB EM, including ordinary firmware. Without BLE use, Controller members are not linked and its tasks are not started. BLE Host, AT, and board GPIO logic are excluded. Wi-Fi and BLE share one radio initialization through `board_radio_init()`.
- `board/` replaces `board.c` through the SDK board overlay mechanism and retains only chip initialization. `board_console_init()` enables the console on demand. `board_wifi_irq_init()` attaches Wi-Fi interrupts separately from `board_init()`, preventing firmware without Wi-Fi from linking wireless libraries.
- `main.c` exists only to complete the SDK link and is not packaged. Wi-Fi initialization follows SDK `examples/wifi/macsw_bare`.
- `link_groups.json` defines optional `wifi` and `ble` link groups, as described below. BLEController enables the BLE group through `ld_libs_ble`.

## Package structure

`include/`, `lib/`, `ld/`, `boot/`, `flags/`, and `manifest.json` are described at the start of `tools/export_package.py`. Consumer requirements:

- Compile: `@flags/c_flags @flags/defines -iprefix <package>/include/ @flags/includes -include <package>/<manifest.forced_include>`.
- Link: `@flags/ld_flags -T <package>/ld/linker.ld -L <package>/ld -L <package>/lib`, with libraries inside `--start-group @flags/ld_libs --end-group`. Wi-Fi programs also add `@flags/ld_libs_wifi` within the group.
- The SDK links every component library with `--whole-archive`; the package avoids this to keep network stacks out of every program. The exporter automatically identifies members that require `--whole-archive` and packages them as `lib/libforce_<group>.a`, linked as a whole through `ld_libs` (base group) or `ld_libs_<group>`. These members include SDK libc ports (overriding newlib `malloc`, `printf`, etc., to keep allocation off newlib sbrk), FreeRTOS `__errno` and pthread, flash ROM patches, Wi-Fi regulatory tables, and version information. See manifest `link.forced` for the list.
- Smoke tests use `tools/check_link.py` to verify that a FreeRTOS-only program links no wifi group archives and uses SDK heap functions, and that the Wi-Fi carrier links every nonempty packaged member present in the native SDK link.
- Image post-processing uses boot2, the partition table, and dts specified by manifest `post_process`.

## Flash tools package

`bl-flash-tools` copies Linux x86_64 `bflb_fw_post_proc`, `BLFlashCommand`, and the selected chip's flash configuration unchanged from `tools/bflb_tools` in the same SDK checkout. See the start of `tools/export_flash_tools.py` for its layout. `BLFlashCommand` modifies its own directory; version 0.2.0 `bl-layout` creates a runtime copy in the build directory, allowing read-only installation directories. `bflb_fw_post_proc` writes only to the image directory.

## Local package index

`tools/make_index.py` archives the platform repository HEAD, `bl616cl-libs`, `bl-flash-tools`, and the toolchain pinned in `sources.json`, generating `package_bouffalolab_index.json` with Linux x86_64 entries only. Archive entries are sorted by name, with timestamps and ownership zeroed; identical inputs produce identical SHA-256 values.

`tools/install_check.sh` verifies installation in temporary directories using this index. It installs into an empty directory through a 127.0.0.1 HTTP server (arduino-cli accepts a `file://` index but not `file://` archives), then uses bubblewrap to disable networking and hide `work/out`, `work/toolchain`, and supplied paths. It checks compilation after installation, after moving the data directory, with read-only `packages/`, and after modifying the Core in the sketchbook while tool package files remain unchanged. Installation requires network access to download arduino-cli built-in tools. The default HTTP port is 8765, configurable with `BL_INDEX_PORT`.

## Outstanding items

- ARDUINO-54 closed the vendor wireless library, toolchain, and tool scope under the user's confirmation that Bouffalo Lab provides licensing. Embedded third-party tools are excluded from further review at the user's request. This scope confirmation is not public release approval; the candidate remains for local review only.
- Flash tools are available only for Linux x86_64.
- The package index has been verified locally but has no public URL.
- Version 0.5.0 has Linux + 1V0 hardware records for BLE HCI/ACL, shutdown/restart, and concurrent Wi-Fi TCP/USB; see the [ARDUINO-43 task](../.tasks/2026-10-26-arduino-43-ble-controller/STATUS.md). ARDUINO-44 subsequently completed 7 official examples and public-address/RAM-key pairing and encryption; 5 examples lack accessories and remain unaccepted. ARDUINO-53 completed the unified candidate regression until 16:00 and BlueZ reconnection using an explicit RA patch. Other platforms, RPA, and key retention across power loss remain unaccepted. Software builds, export, and installation checks do not count as hardware passes.

Local shared references: [Dependency package contract](../docs/development/dependency-release.md), [Development rules](../AGENTS.md), and [Open decisions](../docs/project/open-decisions.md).

## Program Flash storage (ARDUINO-46)

The default profile exports only the littlefs 2.11 engine (disk format 2.1) from the pinned SDK.
It excludes SDK auto-formatting, KV, and libc/VFS adapters.
`LICENSE.littlefs` and engine/layout identities are recorded in the package manifest.
Ordinary sketches consume this archive; the Arduino platform does not invoke an SDK build.

`partition_cfg_4M_lfs_v1.toml` reserves `[0x310000,0x378000)` for littlefs: 416 KiB.
FW0 `[0x10000,0x190000)` and FW1 `[0x190000,0x310000)` are each 1.5 MiB.
`mfg` retains its existing name and shares FW1's address and size: manufacturing test firmware before shipment, OTA slot B afterward.
The media, parameter, and factory addresses remain unchanged.
The filesystem's physical partition name is `lfs`; vendor image tools reject names longer than 7 ASCII characters.
Runtime geometry comes from the active partition table rather than a fixed-address header.
The SDK `CONFIG_BFLB_MTD` component also owns `bflb_boot2.c`, so it is enabled for partition parsing;
filesystem I/O calls Flash directly rather than MTD read/write wrappers.

The ROM link limit excludes the 4 KiB firmware header and 6 KiB ROM header; oversized firmware fails at link time before upload.
Ordinary uploads erase selected regions and do not include a filesystem image.
Layout changes or uploading firmware with an older layout require explicit offline backup and migration;
there is no automatic migration or silent formatting.
See platform `libraries/LittleFS/README.md` for runtime APIs and acceptance boundaries.

`bl-flash-tools 0.2.0` includes standalone `bl-layout`
for Arduino prebuild, post-processing, advanced size, upload, and export recipes.
Maintainers pin its build environment to Python 3.9.17 and `tools/layout-tool-requirements.txt`:

```sh
# Maintenance host only; ordinary Arduino users need neither Python nor SDK.
python3 -m pip install -r tools/layout-tool-requirements.txt
tools/build_layout_tool.sh work/layout-tool
python3 tools/export_flash_tools.py --sdk work/bouffalo_sdk --sources sources.json \
  --chip bl616cl --version 0.2.0 --layout-tool work/layout-tool/dist/bl-layout \
  --out work/out/bl-flash-tools-0.2.0
python3 tools/test_bl_layout.py
```

The executable embeds its interpreter and parsers; runtime licenses are exported with the package.
The tool preserves the selected TOML and its hash, derives ROM capacity limits per build,
checks the post-processed image size, and binds image/partition-table hashes before upload.
A fresh `partition.bin` is mandatory: even when the vendor tool returns zero,
failure to produce a new table must not silently reuse an old one.
Arduino CLI exported companion files and the `bl-flash/` delivery package retain layout identity for standalone upload.
FlashCommand runs from a writable build-directory copy, leaving installed tools unchanged.
See the platform LittleFS README for Default/Custom/explicit-path usage and layout migration limits.

## 0.7.0 network configuration

The profile enables IPv6. A versioned patch makes wl80211 IPv6 output follow `LWIP_IPV6` instead of fhost-only `CFG_IPV6`. The hidden AP patch emits an empty SSID IE when the pinned binary constructs beacons, retains the name in directed probe responses, and restores the hidden flag after startup to reject wildcard probes. This adaptation stays in the SDK; platform/bridge code does not reference binary-internal symbols.

The vendor library contains LTO IR. GCC 10 optimization of internal library calls bypasses GNU `--wrap`. The profile uses `-fno-lto` to select existing native instructions from vendor fat objects and pins SDK and patch SHA values in `sources.json`; it does not rewrite binary instructions. Ordinary Arduino builds still consume only the exported package. Build/map/image checks on 13 ordinary sketches verified that programs not using WiFi do not link the WiFi group. These are auxiliary checks; hardware conclusions are recorded in ARDUINO-49.

## 0.8.0 time and hostname

The profile compiles the pinned SDK `sntp.c` into `liblwip.a`. `LWIP_NETIF_HOSTNAME=1` changes the netif ABI and requires a complete export and matching platform update. SNTP calls newlib `settimeofday` through `board_time.c`. `newlib-mtimer-wall-clock.patch` corrects the original setter's misuse of an RTC alarm API as a counter-setting API and advances UTC with the same system microsecond timer as the Core, avoiding the board's observed RC32K offline drift. The patch SHA is pinned in `sources.json`. It does not reset the RTC, change alarms, or enable TLS certificate date checks.

Arduino sketches still consume only prebuilt packages, without SDK builds or installation-directory changes. Hardware clock, offline resynchronization, DHCP option 12, and TLS regression evidence is in the ARDUINO-45 task. Builds, fake tests, and installation checks are auxiliary only.

## 0.9.0 IPv4 UDP multicast

The profile enables `LWIP_IGMP=1` and fully rebuilds/exports the package. Pinned wl80211 creates STA/AP netifs with IGMP flags; joining through either interface consumes an entry on each. Initial hardware tests reproduced exhaustion of the default 8-entry IGMP pool on the 4th distinct group. With the existing 8 netconns, `MEMP_NUM_IGMP_GROUP = 2 * MEMP_NUM_NETCONN + 2` now provides 18 entries, including two all-hosts entries. Socket limits, stack scheduling, partitions, and TLS policy are unchanged. Membership registration and automatic leave on close retain the SDK implementation.

Ordinary Arduino builds consume matching prebuilt libraries and configuration headers without compiling the SDK. See ARDUINO-50 for real RA WiFiS3/vela multicast, join/leave cycles, broadcast, and UDP/TCP/TLS resource recovery. Software/fake, build, and installation checks are auxiliary evidence only.

## Source CI

`tools/ci/check_sources.py` validates the SDK commit, every SDK patch SHA-256, and the Xuantie toolchain URL/SHA-256 without following a floating branch. `tools/ci/run_source_ci.sh` runs this lock check, builder unit tests, layout tests, shell syntax checks, and source checks for sibling platform/bridge trees when they are present. The three repositories include read-only workflow definitions under `.github/workflows/source-ci.yml`.

These checks produce source evidence only. They do not build or flash a BL616CL image, run a phone or radio test, or approve a release. Firmware compilation and controlled release are separate workflows. The initial CI does not require a new hardware callback; prior functional acceptance remains bound to its recorded firmware.


## Continuous integration

The three public GitHub repositories use `master` for the current source line.
The platform repository also retains its existing `main` and `dev_uno_r4` branches.
Pushes to `master`, pull requests, and manual dispatch run firmware CI on a GitHub-hosted
Ubuntu 22.04 runner. Platform and bridge workflows call the builder workflow at an
explicit commit; the changed repository uses the event commit, and companion inputs
are fixed commit SHAs in the workflow. Change those pins deliberately when updating a
validated combination. Ordinary sketches continue to consume exported packages.

The shared build uses Python 3.9.17, Arduino CLI 1.5.1, the SDK/toolchain identities in
`sources.json`, and the `bl616cl/default` profile. It exports dependency packages and
compiles Blink, SerialEcho, WiFiScan, ControllerUSB, LittleFS Persistence, Preferences
Counter, and the complete UNOR4USBBridgeBL sketch. Image checks run after compilation.
The separate source workflow runs host checks; they do not establish hardware support.

The initial CI intentionally performs fresh builds, without a dependency cache or
hardware callback. A failed command fails the job. Logs, source identities, manifests,
and completed images are retained as Actions build evidence for 14 days, including
partial evidence on failure. They are not stable releases or Arduino package indexes.
Run the same build locally in clean checkouts with the documented Python requirements
and `tools/ci/build_firmware.sh <platform-checkout> <bridge-checkout>`; use a fresh
`BL_BUILDER_WORK` directory for each invocation.

See [controlled releases](docs/releasing.md) for the separate isolated promotion and index rollback procedure.
