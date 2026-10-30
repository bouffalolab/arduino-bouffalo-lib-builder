#!/usr/bin/env bash
# Install the local index into an empty directory, then compile offline with SDK paths hidden.
# Also test relocated data, read-only packages and a modified Core.
# Usage: tools/install_check.sh <arduino-bl-repository> [hidden-path...]
#
# 1. make_index.py archives platform HEAD and work/out packages for a loopback HTTP server.
# CLI accepts file:// indexes but not file:// archives; keep data/downloads/sketchbook paths temporary.
# Build caches are temporary too; installation needs network access for Arduino built-in tools.
# 2. Use bubblewrap to disable networking and hide work/out, work/toolchain and supplied paths.
# Use installed tools only. Compile Blink/SerialEcho and check maps/images each time.
# Test sketches/checkers are export-ignored, so read them from the same platform HEAD as the archive.
# 3. Rename the full data directory and rebuild; then remove packages/ write permission and rebuild.
# Copy the Core into sketchbook hardware/, change main.cpp and confirm dependency-package files remain unchanged.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${BL_BUILDER_WORK:-$ROOT/work}
PLATFORM=$(realpath -e "$1")
shift
HIDE=("$(realpath -e "$WORK/out")" "$(realpath -e "$WORK/toolchain")")
for h in "$@"; do
    HIDE+=("$(realpath -e "$h")")  # Reject nonexistent hidden paths because bwrap would otherwise silently provide ineffective isolation.
done
PORT=${BL_INDEX_PORT:-8765}
FQBN=bouffalolab:bl:unor4wifi_bl616cl
TARBALL=$WORK/toolchain-dl/$(python3 -I -c 'import json, os, sys; print(os.path.basename(json.load(open(sys.argv[1]))["toolchain"]["url"]))' "$ROOT/sources.json")
TMP=$(mktemp -d)
echo "临时目录：$TMP"

python3 "$ROOT/tools/make_index.py" --platform "$PLATFORM" --libs "$WORK/out/bl616cl-libs" \
    --flash-tools "$WORK/out/bl-flash-tools" --sources "$ROOT/sources.json" --toolchain-tarball "$TARBALL" \
    --base-url "http://127.0.0.1:$PORT" --out "$TMP/index"

# git show reads files without changing the platform index or worktree.
TESTS=$TMP/platform-tests
for f in $(git -C "$PLATFORM" ls-tree -r --name-only HEAD tests/sketches tests/check_map.py tests/check_image.py); do
    mkdir -p "$(dirname "$TESTS/$f")"
    git -C "$PLATFORM" show "HEAD:$f" >"$TESTS/$f"
done

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$TMP/index" >"$TMP/http.log" 2>&1 &
SERVER=$!
# Remove this script's temporary resources and restore its saved local state.
cleanup() {
    kill "$SERVER" 2>/dev/null || true
    chmod -R u+w "$TMP" 2>/dev/null || true
}
trap cleanup EXIT

# Write an isolated Arduino CLI configuration for package-install verification.
write_config() {
    cat >"$TMP/arduino-cli.yaml" <<EOF
board_manager:
  additional_urls:
    - file://$TMP/index/package_bouffalolab_index.json
build_cache:
  path: $TMP/cache
directories:
  data: $1
  downloads: $TMP/downloads
  user: $TMP/sketchbook
EOF
}
CLI=(arduino-cli --config-file "$TMP/arduino-cli.yaml")
BWRAP=(bwrap --dev-bind / / --unshare-net)
for h in "${HIDE[@]}"; do
    BWRAP+=(--tmpfs "$h")
done

fail=0
# Print a tagged verification result for this script.
report() {  # $1: check name; $2: exit status.
    if [ "$2" = 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

# Installed tool files must match their manifest: no missing, modified or additional files.
check_tools() {
    python3 -I - "$1"/tools/bl616cl-libs/* "$1"/tools/bl-flash-tools/* <<'EOF'
import hashlib, json, pathlib, sys
ok = True
for d in map(pathlib.Path, sys.argv[1:]):
    m = json.loads((d / "manifest.json").read_text())
    listed = {f["path"]: f["sha256"] for f in m["files"]}
    on_disk = {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()} - {"manifest.json"}
    bad = sorted(p for p in listed if p not in on_disk or hashlib.sha256((d / p).read_bytes()).hexdigest() != listed[p])
    extra = sorted(on_disk - set(listed))
    print("  %s %s：%d 个文件，改动或缺失 %s，多出 %s" % (m["name"], m["version"], len(listed), bad, extra))
    ok = ok and not bad and not extra
sys.exit(0 if ok else 1)
EOF
}

# Compile test sketches offline with SDK paths hidden; $1 is the result label.
compile_all() {
    local label=$1 data libs name out rc
    data=$(sed -n 's/^  data: //p' "$TMP/arduino-cli.yaml")
    libs=$(echo "$data"/packages/bouffalolab/tools/bl616cl-libs/*)
    for name in Blink SerialEcho; do
        out=$TMP/build-$label-$name
        rc=0
        "${BWRAP[@]}" "${CLI[@]}" compile -b "$FQBN" --build-path "$out" "$TESTS/tests/sketches/$name" \
            >"$out.log" 2>&1 || rc=$?
        if [ "$rc" = 0 ]; then
            python3 -I "$TESTS/tests/check_map.py" "$libs/manifest.json" "$out/$name.ino.map" >"$out.check" || rc=$?
            python3 -I "$TESTS/tests/check_image.py" "$out" "$name.ino" >>"$out.check" || rc=$?
        fi
        report "$label 编译 $name（日志 $out.log）" "$rc"
        [ -f "$out.check" ] && sed 's/^/  /' "$out.check"
    done
}

write_config "$TMP/data"
mkdir -p "$TMP/sketchbook"
for _ in 1 2 3 4 5; do
    curl -fsS -o /dev/null "http://127.0.0.1:$PORT/package_bouffalolab_index.json" && break
    sleep 1
done
"${CLI[@]}" core update-index >"$TMP/install.log" 2>&1
"${CLI[@]}" core install "bouffalolab:bl" >>"$TMP/install.log" 2>&1
kill "$SERVER"
PLAT_DIR=$(echo "$TMP"/data/packages/bouffalolab/hardware/bl/*)
echo "安装：$(basename "$PLAT_DIR")，日志 $TMP/install.log"
rc=0; check_tools "$TMP/data/packages/bouffalolab" || rc=$?
report "安装的工具包与 manifest 一致" "$rc"
compile_all installed

mv "$TMP/data" "$TMP/data-moved"
write_config "$TMP/data-moved"
PLAT_DIR=$(echo "$TMP"/data-moved/packages/bouffalolab/hardware/bl/*)
compile_all moved

chmod -R a-w "$TMP/data-moved/packages"
compile_all readonly
chmod -R u+w "$TMP/data-moved/packages"

# A sketchbook platform overrides the installed platform while tools still come from the installation.
DEV=$TMP/sketchbook/hardware/bouffalolab/bl
mkdir -p "$(dirname "$DEV")"
cp -r "$PLAT_DIR" "$DEV"
echo 'extern "C" void install_check_core_marker(void) {}' >>"$DEV/cores/bouffalo/main.cpp"
compile_all core-modified
# The marker function exists only in the sketchbook Core; finding it in the object proves the changed Core was used.
rc=0; grep -aq install_check_core_marker "$TMP/build-core-modified-Blink/core/main.cpp.o" || rc=$?
report "修改 Core 后编译的是 sketchbook 中改过的 main.cpp" "$rc"
rc=0; check_tools "$TMP/data-moved/packages/bouffalolab" || rc=$?
report "修改 Core 编译后工具包仍与 manifest 一致" "$rc"

exit $fail
