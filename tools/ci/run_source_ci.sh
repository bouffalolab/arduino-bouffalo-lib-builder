#!/usr/bin/env bash
# Run deterministic source checks. This does not build, flash, or publish firmware.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PLATFORM=${ARDUINO_BL_PLATFORM:-$ROOT/../arduino-bl}
BRIDGE=${ARDUINO_BL_BRIDGE:-$ROOT/../uno-r4-wifi-usb-bridge-bl}
OUT=${CI_ARTIFACT_DIR:-$ROOT/work/ci-artifacts}
mkdir -p "$OUT"
PYTHONPATH="$ROOT/tools" python3 -I "$ROOT/tools/ci/check_sources.py" --output "$OUT/source-lock.json"
PYTHONPATH="$ROOT/tools" python3 -I -m unittest discover -s "$ROOT/tools" -p 'test_*.py'
PYTHONPATH="$ROOT/tools" python3 "$ROOT/tools/test_bl_layout.py"
python3 -I -m compileall -q "$ROOT/tools"
bash -n "$ROOT/build.sh" "$ROOT/tools/build_layout_tool.sh" "$ROOT/tools/install_check.sh" "$ROOT/tools/smoke_test.sh"
if [ -d "$PLATFORM/tests" ]; then
    bash -n "$PLATFORM/tests/compile_check.sh"
fi
if [ -d "$BRIDGE/tests" ]; then
    python3 -I -m compileall -q "$BRIDGE/tests"
fi
printf 'source CI checks passed; hardware and remote workflow remain separate gates\n'
