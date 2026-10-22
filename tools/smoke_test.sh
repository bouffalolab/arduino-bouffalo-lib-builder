#!/usr/bin/env bash
# 底层包冒烟测试：只用包内文件编译、链接两个程序。
# 用法：tools/smoke_test.sh <包目录> <工具链 bin 目录> <SDK 原生 ELF> [隐藏路径...]
#
# 1. 把包复制到新的临时路径，验证可重定位；
# 2. 在 bubblewrap 中把隐藏路径（SDK 检出、构建目录等）换成空目录后编译；
# 3. rtos：只用 FreeRTOS，检查 main 来自测试程序、未链入网络栈与 Wi-Fi 对象；
# 4. wifi：用 profile 的载体 main.c，检查不用 --whole-archive 也能闭合链接，
#    并列出 SDK 原生 ELF 中非空、但本次链接缺失或变空的输出段。
set -euo pipefail

PKG_SRC=$(realpath "$1")
TC_BIN=$(realpath "$2")
REF_ELF=$(realpath "$3")
shift 3
HIDE=("$@")

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
    "$GCC" @"$PKG/flags/c_flags" @"$PKG/flags/defines" -iprefix "$PKG/include/" @"$PKG/flags/includes" \
        -include "$PKG/include/build/generated/autoconfig.h" -std=gnu99 -c "$prog.c" -o "$prog.o"
    "$GCC" @"$PKG/flags/ld_flags" -T "$PKG/ld/linker.ld" -L "$PKG/ld" -L "$PKG/lib" "$prog.o" \
        -Wl,--start-group @"$PKG/flags/ld_libs" -Wl,--end-group -Wl,-Map="$prog.map" -Wl,-y,main \
        -o "$prog.elf" 2>"$prog.trace"
done
EOF

BWRAP=(bwrap --dev-bind / /)
for h in "${HIDE[@]}"; do
    BWRAP+=(--tmpfs "$(realpath "$h")")
done
"${BWRAP[@]}" bash "$TMP/build.sh" "$TMP" "$PKG" "$TC_BIN"

fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }

"$TC_BIN/riscv64-unknown-elf-nm" "$TMP/wifi.elf" >"$TMP/wifi.nm"
check "rtos: main 由测试程序定义" 'grep -q ": rtos.o: definition of main$" "$TMP/rtos.trace"'
check "rtos: 未链入 lwip/wifi6/fhost/macsw/wpa 对象" \
    '! grep -E "/lib(lwip|wifi6_lwip_adapter|fhost_[a-z0-9_]+|macsw_[a-z0-9_]+|wpa_supplicant)\.a\(" "$TMP/rtos.map" >/dev/null'
check "wifi: 链接闭合并含 fhost_init" 'grep -q " T fhost_init$" "$TMP/wifi.nm"'

echo "-- 段对比（SDK 原生 ELF 非空，本次 wifi.elf 缺失或为 0）"
sections() { "$TC_BIN/riscv64-unknown-elf-size" -A "$1" | awk 'NR>2 && $1 ~ /^\./ {print $1, $2}'; }
join -a1 -e 0 -o 0,1.2,2.2 <(sections "$REF_ELF" | sort) <(sections "$TMP/wifi.elf" | sort) |
    awk '$2 > 0 && $3 == 0 {print "  " $1 " ref=" $2 " ours=" $3; missing=1} END {if (!missing) print "  无"}'

echo "-- 尺寸"
"$TC_BIN/riscv64-unknown-elf-size" "$TMP/rtos.elf" "$TMP/wifi.elf" "$REF_ELF"
echo "临时目录：$TMP"
exit $fail
