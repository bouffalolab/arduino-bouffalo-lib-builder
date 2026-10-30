#!/usr/bin/env bash
# Build one dependency-package profile.
# Usage: ./build.sh [<chip>/<profile>], default bl616cl/default.
# Fetch the sources.json-pinned SDK/toolchain into work/; build under work/build/.
# Do not modify the checkout. prepare_sdk_tree.py creates <build-directory>.sdk for the build.
# Apply sources.json SDK patches only to that derived tree. BL_BUILDER_WORK relocates work/.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
WORK=${BL_BUILDER_WORK:-$ROOT/work}
PROFILE=${1:-bl616cl/default}
PROFILE_DIR=$ROOT/profiles/$PROFILE
SDK=$WORK/bouffalo_sdk
BUILD=$WORK/build/${PROFILE//\//-}
SDK_TREE=$BUILD.sdk

[ -d "$PROFILE_DIR" ] || { echo "unknown profile: $PROFILE" >&2; exit 2; }

# Read one pinned dependency field from sources.json.
src() { python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' "$ROOT/sources.json" "$1" "$2"; }

# Fetch and check out the pinned Bouffalo SDK revision in the build cache.
fetch_sdk() {
    local repo commit head
    repo=$(src sdk repo); commit=$(src sdk commit)
    if [ ! -d "$SDK/.git" ]; then
        git init -q "$SDK"
        git -C "$SDK" remote add origin "$repo"
    fi
    # Shallow-fetch and checkout the pinned commit; sdk.ref records provenance only. Require a clean checkout first.
    head=$(git -C "$SDK" rev-parse -q --verify HEAD || true)
    if [ "$head" != "$commit" ]; then
        [ -z "$(git -C "$SDK" status --porcelain --ignored)" ] || { echo "SDK checkout is not clean: $SDK" >&2; exit 1; }
        git -C "$SDK" fetch --depth 1 origin "$commit"
        git -C "$SDK" checkout -q --detach "$commit"
    fi
    head=$(git -C "$SDK" rev-parse HEAD)
    [ "$head" = "$commit" ] || { echo "SDK HEAD $head != pinned $commit" >&2; exit 1; }
    # Include ignored files: require an empty status before/after builds to detect any writes into the SDK checkout.
    [ -z "$(git -C "$SDK" status --porcelain --ignored)" ] || { echo "SDK checkout is not clean: $SDK" >&2; exit 1; }
}

# Obtain the pinned toolchain archive and verify its checksum before extraction.
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
        rm -f "$tarball"  # Download again next time.
        echo "toolchain sha256 mismatch, removed: $tarball" >&2; exit 1
    fi
    if [ ! -d "$TOOLCHAIN" ]; then
        # Extract into a temporary directory and rename only when complete, avoiding partial toolchains after interruption.
        local tmp
        mkdir -p "$WORK/toolchain"
        tmp=$(mktemp -d "$WORK/toolchain/.extract.XXXXXX")
        tar -xJf "$tarball" -C "$tmp"
        mv "$tmp/$dir" "$TOOLCHAIN"
        rmdir "$tmp"
    fi
}

# Prepare the selected SDK profile and build its dependency carrier.
build_profile() {
    rm -rf "$BUILD"
    mkdir -p "$(dirname "$BUILD")"
    cp -r "$PROFILE_DIR" "$BUILD"
    python3 -I "$ROOT/tools/prepare_sdk_tree.py" --sdk "$SDK" --sources "$ROOT/sources.json" --out "$SDK_TREE"
    # SDK git version generation reads the checkout identity and derived tree; patched versions retain the dirty marker.
    # BL_PREFIX_MAP_ROOT maps work/ to /work so libraries contain no host build paths.
    GIT_DIR=$SDK/.git GIT_WORK_TREE=$SDK_TREE PATH=$TOOLCHAIN/bin:$PATH PYTHONDONTWRITEBYTECODE=1 BL_PREFIX_MAP_ROOT=$WORK \
        make -C "$BUILD" BL_SDK_BASE="$SDK_TREE" VERBOSE=1 >"$BUILD.log" 2>&1 \
        || { grep -E -m 20 'error:|undefined reference|Error [0-9]' "$BUILD.log" >&2 || true
             echo "build failed, log: $BUILD.log" >&2; exit 1; }
    [ -z "$(git -C "$SDK" status --porcelain --ignored)" ] || { echo "build modified SDK checkout: $SDK" >&2; exit 1; }
}

fetch_sdk
fetch_toolchain
build_profile
echo "built $PROFILE in $BUILD (log: $BUILD.log)"
