#!/usr/bin/env bash
# Build pinned dependencies and representative Arduino firmware; never flash or publish.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PLATFORM=$(realpath -e "${1:?platform checkout required}")
BRIDGE=$(realpath -e "${2:?bridge checkout required}")
WORK=${BL_BUILDER_WORK:-$ROOT/work}
OUT=$WORK/ci-artifacts
mkdir -p "$OUT"

python3 -I "$ROOT/tools/ci/check_sources.py" --output "$OUT/source-lock.json"
python3 - "$ROOT" "$PLATFORM" "$BRIDGE" "$OUT/commits.json" <<'PY'
import json, subprocess, sys
from pathlib import Path
commits = {}
for name, path in zip(('builder', 'platform', 'bridge'), sys.argv[1:4]):
    if subprocess.check_output(['git', '-C', path, 'status', '--porcelain']).strip():
        raise SystemExit('source checkout is dirty: ' + name)
    commits[name] = subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip()
Path(sys.argv[4]).write_text(json.dumps(commits, indent=2) + '\n')
PY

"$ROOT/build.sh" bl616cl/default
TC=$WORK/toolchain/$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["toolchain"]["dir"])' "$ROOT/sources.json")
python3 -I "$ROOT/tools/export_package.py" \
    --build "$WORK/build/bl616cl-default" --build-log "$WORK/build/bl616cl-default.log" \
    --sdk "$WORK/bouffalo_sdk" --sdk-tree "$WORK/build/bl616cl-default.sdk" \
    --sources "$ROOT/sources.json" --toolchain-bin "$TC/bin" \
    --profile bl616cl/default --version 0.12.0 --out "$WORK/out/bl616cl-libs"
"$ROOT/tools/build_layout_tool.sh" "$WORK/layout-tool" >"$OUT/layout-tool.log" 2>&1
python3 -I "$ROOT/tools/export_flash_tools.py" \
    --sdk "$WORK/bouffalo_sdk" --sources "$ROOT/sources.json" --chip bl616cl \
    --layout-tool "$WORK/layout-tool/dist/bl-layout" --version 0.3.0 --out "$WORK/out/bl-flash-tools"

# Arduino CLI's standard sketchbook platform layout; dependencies are explicit packages.
mkdir -p "$WORK/arduino/user/hardware/bouffalolab" "$WORK/arduino/data" "$WORK/arduino/downloads"
ln -s "$PLATFORM" "$WORK/arduino/user/hardware/bouffalolab/bl"
cat >"$WORK/arduino/arduino-cli.yaml" <<EOF
directories:
  user: $WORK/arduino/user
  data: $WORK/arduino/data
  downloads: $WORK/arduino/downloads
EOF
CLI=(arduino-cli --config-file "$WORK/arduino/arduino-cli.yaml")
"${CLI[@]}" core update-index
for sketch in \
    "$PLATFORM/tests/sketches/Blink" \
    "$PLATFORM/tests/sketches/SerialEcho" \
    "$PLATFORM/tests/sketches/WiFiScan" \
    "$PLATFORM/libraries/BLEController/examples/ControllerUSB" \
    "$PLATFORM/libraries/LittleFS/examples/Persistence" \
    "$PLATFORM/libraries/Preferences/examples/Counter" \
    "$PLATFORM/libraries/BLUpdate/examples/SerialUpdate" \
    "$BRIDGE/UNOR4USBBridgeBL"; do
    name=$(basename "$sketch")
    echo "Building $name"
    "${CLI[@]}" compile -b bouffalolab:bl:unor4wifi_bl616cl \
        --build-property "runtime.tools.bl616cl-libs.path=$WORK/out/bl616cl-libs" \
        --build-property "runtime.tools.gcc-riscv.path=$TC" \
        --build-property "runtime.tools.bl-flash-tools.path=$WORK/out/bl-flash-tools" \
        --build-path "$WORK/firmware/$name" "$sketch" >"$OUT/$name.log" 2>&1 \
        || { cat "$OUT/$name.log"; exit 1; }
    python3 -I "$PLATFORM/tests/check_image.py" "$WORK/firmware/$name" "$name.ino"
    mkdir -p "$OUT/firmware/$name"
    cp "$WORK/firmware/$name/$name.ino.bin" "$OUT/firmware/$name/"
    cp "$WORK/firmware/$name/partition.bin" "$OUT/firmware/$name/"
    sha256sum "$OUT/firmware/$name/"* >>"$OUT/firmware-sha256.txt"
done
cp "$WORK/out/bl616cl-libs/manifest.json" "$OUT/libs-manifest.json"
cp "$WORK/out/bl-flash-tools/manifest.json" "$OUT/flash-tools-manifest.json"
echo 'Firmware builds passed. Build evidence only; no hardware test or release performed.'
