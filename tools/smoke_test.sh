#!/usr/bin/env bash
# Smoke-test dependency linking using only packaged files for two programs.
# Usage: tools/smoke_test.sh <package> <toolchain-bin> <native-SDK-ELF> [hidden-path...]
#
# 1. Copy the package to a temporary root to check relocation.
# 2. Hide original package/SDK/build paths with bubblewrap before compilation.
# 3. rtos uses FreeRTOS only; main must come from the test and heap functions from the SDK libc port.
# No archive member from the Wi-Fi link group may be included.
# 4. wifi uses carrier main.c and ld_libs_wifi to check link closure without whole-archiving every archive.
# Require the native reference's contributing archive members and nonempty output sections.
set -euo pipefail

PKG_SRC=$(realpath -e "$1")
TC_BIN=$(realpath -e "$2")
REF_ELF=$(realpath -e "$3")
REF_MAP=${REF_ELF%.elf}.map
shift 3
HIDE=("$PKG_SRC")
for h in "$@"; do
    HIDE+=("$(realpath -e "$h")")  # Reject nonexistent hidden paths; bwrap would otherwise silently skip isolation.
done
[ -f "$REF_MAP" ] || { echo "missing $REF_MAP" >&2; exit 2; }

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
PKG=$TMP/relocated/bl616cl-libs
mkdir -p "$(dirname "$PKG")"
cp -r "$PKG_SRC" "$PKG"
cp "$ROOT/profiles/bl616cl/default/main.c" "$TMP/wifi.c"
cat >"$TMP/rtos.c" <<'EOF'
#include "board.h"
#include "FreeRTOS.h"
#include "task.h"

static void idle_loop(void *arg)
{
    for (;;) {
        vTaskDelay(100);
    }
}

int main(void)
{
    board_init();
    xTaskCreate(idle_loop, "t", 512, NULL, 5, NULL);
    vTaskStartScheduler();
    for (;;) {
    }
}
EOF

cat >"$TMP/build.sh" <<'EOF'
set -euo pipefail
cd "$1"; PKG=$2; GCC=$3/riscv64-unknown-elf-gcc
for prog in rtos wifi; do
    extra=()
    [ "$prog" = wifi ] && extra=(@"$PKG/flags/ld_libs_wifi")
    "$GCC" @"$PKG/flags/c_flags" @"$PKG/flags/defines" -iprefix "$PKG/include/" @"$PKG/flags/includes" \
        -include "$PKG/include/build/generated/autoconfig.h" -std=gnu99 -c "$prog.c" -o "$prog.o"
    "$GCC" @"$PKG/flags/ld_flags" -T "$PKG/ld/linker.ld" -L "$PKG/ld" -L "$PKG/lib" "$prog.o" \
        -Wl,--start-group "${extra[@]}" @"$PKG/flags/ld_libs" -Wl,--end-group -Wl,-Map="$prog.map" \
        -Wl,-y,main -Wl,-y,_malloc_r -o "$prog.elf" 2>"$prog.trace"
done
EOF

BWRAP=(bwrap --dev-bind / /)
for h in "${HIDE[@]}"; do
    BWRAP+=(--tmpfs "$h")
done
"${BWRAP[@]}" bash "$TMP/build.sh" "$TMP" "$PKG" "$TC_BIN"

fail=0
# Validate the selected artifact or fixture and raise an error on a failed invariant.
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
CHECK_LINK=(python3 -I "$ROOT/tools/check_link.py")

"$TC_BIN/riscv64-unknown-elf-nm" "$TMP/wifi.elf" >"$TMP/wifi.nm"
check "rtos: main 由测试程序定义" 'grep -q ": rtos.o: definition of main$" "$TMP/rtos.trace"'
check "rtos: _malloc_r 来自 SDK libc 移植层" \
    'grep -q "(liblibc.a@port_memory.c.obj): definition of _malloc_r$" "$TMP/rtos.trace"'
check "rtos: 未链入 wifi 链接组的归档" '"${CHECK_LINK[@]}" absent "$PKG/manifest.json" wifi "$TMP/rtos.map"'
check "wifi: 链接闭合并含 wl80211_init" 'grep -q " T wl80211_init$" "$TMP/wifi.nm"'
check "wifi: SDK 原生链接中有内容的包内成员都已链入" \
    '"${CHECK_LINK[@]}" parity "$PKG/manifest.json" "$REF_MAP" "$TMP/wifi.map"'

# Inspect expected image sections with the selected cross-toolchain.
sections() { "$TC_BIN/riscv64-unknown-elf-size" -A "$1" | awk 'NR>2 && $1 ~ /^\./ {print $1, $2}'; }
join -a1 -e 0 -o 0,1.2,2.2 <(sections "$REF_ELF" | sort) <(sections "$TMP/wifi.elf" | sort) |
    awk '$2 > 0 && $3 == 0 {print "  " $1 " ref=" $2 " ours=" $3}' >"$TMP/missing_sections"
cat "$TMP/missing_sections"
check "wifi: 原生 ELF 中非空的输出段都存在" '[ ! -s "$TMP/missing_sections" ]'

echo "-- 尺寸"
"$TC_BIN/riscv64-unknown-elf-size" "$TMP/rtos.elf" "$TMP/wifi.elf" "$REF_ELF"
echo "临时目录：$TMP"
exit $fail
