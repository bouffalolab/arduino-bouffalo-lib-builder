#!/usr/bin/env bash
# 构建一个底层包 profile。
# 用法：./build.sh [<chip>/<profile>]，默认 bl616cl/default。
# SDK 与工具链按 sources.json 固定版本获取到 work/，构建在 work/build/ 中进行，
# 不修改 SDK 检出目录。BL_BUILDER_WORK 可改变 work 目录位置。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
WORK=${BL_BUILDER_WORK:-$ROOT/work}
PROFILE=${1:-bl616cl/default}
PROFILE_DIR=$ROOT/profiles/$PROFILE
SDK=$WORK/bouffalo_sdk
BUILD=$WORK/build/${PROFILE//\//-}

[ -d "$PROFILE_DIR" ] || { echo "unknown profile: $PROFILE" >&2; exit 2; }

src() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' "$ROOT/sources.json" "$1" "$2"; }

fetch_sdk() {
    local repo tag commit head
    repo=$(src sdk repo); tag=$(src sdk tag); commit=$(src sdk commit)
    if [ ! -d "$SDK/.git" ]; then
        git clone --branch "$tag" --depth 1 "$repo" "$SDK"
    fi
    head=$(git -C "$SDK" rev-parse HEAD)
    [ "$head" = "$commit" ] || { echo "SDK HEAD $head != pinned $commit" >&2; exit 1; }
    # 含被忽略的文件：构建前后都要求为空，才能发现构建在 SDK 中写入的任何文件
    [ -z "$(git -C "$SDK" status --porcelain --ignored)" ] || { echo "SDK checkout is not clean: $SDK" >&2; exit 1; }
}

fetch_toolchain() {
    local url sha dir tarball
    url=$(src toolchain url); sha=$(src toolchain sha256); dir=$(src toolchain dir)
    TOOLCHAIN=$WORK/toolchain/$dir
    tarball=$WORK/toolchain-dl/$(basename "$url")
    if [ ! -f "$tarball" ]; then
        mkdir -p "$WORK/toolchain-dl"
        curl -fL --retry 2 -o "$tarball.part" "$url"
        mv "$tarball.part" "$tarball"
    fi
    if ! echo "$sha  $tarball" | sha256sum -c --quiet -; then
        rm -f "$tarball"  # 下次重新下载
        echo "toolchain sha256 mismatch, removed: $tarball" >&2; exit 1
    fi
    if [ ! -d "$TOOLCHAIN" ]; then
        # 先解压到临时目录，完整后再改名，中断时不会留下残缺的工具链目录
        local tmp
        mkdir -p "$WORK/toolchain"
        tmp=$(mktemp -d "$WORK/toolchain/.extract.XXXXXX")
        tar -xJf "$tarball" -C "$tmp"
        mv "$tmp/$dir" "$TOOLCHAIN"
        rmdir "$tmp"
    fi
}

build_profile() {
    rm -rf "$BUILD"
    mkdir -p "$(dirname "$BUILD")"
    cp -r "$PROFILE_DIR" "$BUILD"
    PATH=$TOOLCHAIN/bin:$PATH PYTHONDONTWRITEBYTECODE=1 \
        make -C "$BUILD" BL_SDK_BASE="$SDK" VERBOSE=1 >"$BUILD.log" 2>&1 \
        || { grep -E -m 20 'error:|undefined reference|Error [0-9]' "$BUILD.log" >&2 || true
             echo "build failed, log: $BUILD.log" >&2; exit 1; }
    [ -z "$(git -C "$SDK" status --porcelain --ignored)" ] || { echo "build modified SDK checkout: $SDK" >&2; exit 1; }
}

fetch_sdk
fetch_toolchain
build_profile
echo "built $PROFILE in $BUILD (log: $BUILD.log)"
