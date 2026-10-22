#!/usr/bin/env bash
# 安装链检查：从本地包索引把平台安装到空目录，然后在断网、隐藏 SDK 的环境中编译，
# 并依次检查移动安装目录、只读安装、修改 Core 三种情况。
# 用法：tools/install_check.sh <arduino-bl 仓库> [要隐藏的路径...]
#
# 1. make_index.py 用平台仓库 HEAD 与 work/out 下的包生成归档和索引，经 127.0.0.1 的 HTTP 服务提供
#    （arduino-cli 接受 file:// 索引，不接受 file:// 归档）；arduino-cli 的数据、下载、sketchbook、
#    构建缓存目录都在临时目录。安装时需要联网下载 arduino-cli 自带工具。
# 2. 编译在 bubblewrap 中进行：--unshare-net 断网，work/out、work/toolchain 和给出的路径换成空目录，
#    只能用安装目录里的工具。每次编译 Blink、SerialEcho，并用安装的平台中的 check_map.py、check_image.py 检查。
# 3. 移动：把整个数据目录改名后重新编译。只读：去掉 packages/ 的写权限后编译。
#    修改 Core：把安装的平台复制到 sketchbook 的 hardware/ 并改动 main.cpp，编译后核对底层包文件未变。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=${BL_BUILDER_WORK:-$ROOT/work}
PLATFORM=$(realpath -e "$1")
shift
HIDE=("$(realpath -e "$WORK/out")" "$(realpath -e "$WORK/toolchain")")
for h in "$@"; do
    HIDE+=("$(realpath -e "$h")")  # 路径写错时 bwrap 不报错，隔离会静默失效
done
PORT=${BL_INDEX_PORT:-8765}
FQBN=bouffalolab:bl:unor4wifi_bl616cl
TARBALL=$WORK/toolchain-dl/$(python3 -I -c 'import json, os, sys; print(os.path.basename(json.load(open(sys.argv[1]))["toolchain"]["url"]))' "$ROOT/sources.json")
TMP=$(mktemp -d)
echo "临时目录：$TMP"

python3 "$ROOT/tools/make_index.py" --platform "$PLATFORM" --libs "$WORK/out/bl616cl-libs" \
    --flash-tools "$WORK/out/bl-flash-tools" --sources "$ROOT/sources.json" --toolchain-tarball "$TARBALL" \
    --base-url "http://127.0.0.1:$PORT" --out "$TMP/index"

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$TMP/index" >"$TMP/http.log" 2>&1 &
SERVER=$!
cleanup() {
    kill "$SERVER" 2>/dev/null || true
    chmod -R u+w "$TMP" 2>/dev/null || true
}
trap cleanup EXIT

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
report() {  # $1 = 检查名，$2 = 退出码
    if [ "$2" = 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

# 安装的工具包与其 manifest 一致：没有缺失、改动或多出的文件
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

# 断网、隐藏 SDK 编译测试 sketch；$1 = 标签，$2 = 提供 tests/ 的平台目录
compile_all() {
    local label=$1 plat=$2 data libs name out rc
    data=$(sed -n 's/^  data: //p' "$TMP/arduino-cli.yaml")
    libs=$(echo "$data"/packages/bouffalolab/tools/bl616cl-libs/*)
    for name in Blink SerialEcho; do
        out=$TMP/build-$label-$name
        rc=0
        "${BWRAP[@]}" "${CLI[@]}" compile -b "$FQBN" --build-path "$out" "$plat/tests/sketches/$name" \
            >"$out.log" 2>&1 || rc=$?
        if [ "$rc" = 0 ]; then
            python3 -I "$plat/tests/check_map.py" "$libs/manifest.json" "$out/$name.ino.map" >"$out.check" || rc=$?
            python3 -I "$plat/tests/check_image.py" "$out" "$name.ino" >>"$out.check" || rc=$?
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
compile_all installed "$PLAT_DIR"

mv "$TMP/data" "$TMP/data-moved"
write_config "$TMP/data-moved"
PLAT_DIR=$(echo "$TMP"/data-moved/packages/bouffalolab/hardware/bl/*)
compile_all moved "$PLAT_DIR"

chmod -R a-w "$TMP/data-moved/packages"
compile_all readonly "$PLAT_DIR"
chmod -R u+w "$TMP/data-moved/packages"

# 修改 Core：sketchbook 中的同名平台优先于安装的平台，工具仍来自安装目录
DEV=$TMP/sketchbook/hardware/bouffalolab/bl
mkdir -p "$(dirname "$DEV")"
cp -r "$PLAT_DIR" "$DEV"
echo 'extern "C" void install_check_core_marker(void) {}' >>"$DEV/cores/bouffalo/main.cpp"
compile_all core-modified "$DEV"
# 标记函数只存在于 sketchbook 中的 main.cpp，出现在目标文件里说明用的是改过的 Core
rc=0; grep -aq install_check_core_marker "$TMP/build-core-modified-Blink/core/main.cpp.o" || rc=$?
report "修改 Core 后编译的是 sketchbook 中改过的 main.cpp" "$rc"
rc=0; check_tools "$TMP/data-moved/packages/bouffalolab" || rc=$?
report "修改 Core 编译后工具包仍与 manifest 一致" "$rc"

exit $fail
