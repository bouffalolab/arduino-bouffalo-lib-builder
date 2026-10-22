# arduino-bl-lib-builder

为 arduino-bl 构建和发布版本化底层依赖包。完整 SDK 的获取、配置和补丁只在此类维护者流程中处理。

当前阶段：能构建并导出 BL616CL 首个本地包 `bl616cl-libs`（profile `default`：FreeRTOS、lwIP、Wi-Fi STA、CherryUSB device CDC，BLE 关闭），尚未发布。

## 使用

```sh
./build.sh                          # 获取固定版本的 SDK 与工具链，构建 profiles/bl616cl/default
python3 tools/export_package.py \
  --build work/build/bl616cl-default --build-log work/build/bl616cl-default.log \
  --sdk work/bouffalo_sdk --sources sources.json \
  --toolchain-bin work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  --profile bl616cl/default --version 0.2.0 --out work/out/bl616cl-libs
tools/smoke_test.sh work/out/bl616cl-libs \
  work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  work/build/bl616cl-default/build/build_out/arduino_bl_profile_bl616cl.elf \
  work/bouffalo_sdk work/build      # 之后的参数是测试时要隐藏的路径，原包目录会自动隐藏
python3 tools/test_export_package.py # 导出逻辑的单元测试；设置 BL_TEST_GCC 时额外用真实 gcc 检查
```

`sources.json` 固定 SDK（公网 bouffalo_sdk v2.3.37）和工具链（Xuantie GCC 10.2.0 V2.6.1）的来源与校验值。`work/` 存放检出、下载和构建输出，不入库。构建在 `work/build/` 中进行；构建前后 SDK 检出目录都必须没有任何改动或新增文件（含被忽略的文件），否则 `build.sh` 报错。导出时核对 SDK 检出与 `sources.json` 一致；`--out` 只会替换空目录或以前的导出。

## profile

`profiles/<chip>/<profile>/` 是一个 SDK 工程：

- `defconfig` 决定组件组合。
- `FreeRTOSConfig.h`、`lwipopts_user.h`、`usb_config.h`、`mbedtls_sample_config.h` 会改变库内布局，随包导出，平台必须使用包内这一份。
- `board/` 通过 SDK 的 board overlay 机制替换 `board.c`，只保留芯片级初始化。控制台改由 `board_console_init()` 按需开启；Wi-Fi 中断由 `board_wifi_irq_init()` 挂接，与 `board_init()` 分开，避免不用 Wi-Fi 的固件链入无线库。
- `main.c` 只用于让 SDK 完成一次链接，不进入包。
- `link_groups.json` 定义链接组（目前只有 `wifi`），见下文“链接”。

## 包结构

`include/`、`lib/`、`ld/`、`boot/`、`flags/` 和 `manifest.json`，说明见 `tools/export_package.py` 开头。使用方的要点：

- 编译：`@flags/c_flags @flags/defines -iprefix <包>/include/ @flags/includes -include <包>/<manifest.forced_include>`。
- 链接：`@flags/ld_flags -T <包>/ld/linker.ld -L <包>/ld -L <包>/lib`，库放在 `--start-group @flags/ld_libs --end-group` 中；使用 Wi-Fi 的程序在组内再加 `@flags/ld_libs_wifi`。
- SDK 以 `--whole-archive` 链接全部组件库；包不这样做，以免每个程序都带入网络栈。只靠 `--whole-archive` 才生效的成员由导出脚本自动找出，打成 `lib/libforce_<组>.a`，由 `ld_libs`（base 组）或 `ld_libs_<组>` 整体链入。这类成员包括 SDK libc 移植层（覆盖 newlib 的 `malloc`、`printf` 等，否则堆会落到 newlib 的 sbrk 上）、FreeRTOS 的 `__errno` 与 pthread、flash ROM 补丁、Wi-Fi 监管域表和版本信息。清单见 manifest 的 `link.forced`。
- 冒烟测试用 `tools/check_link.py` 检查：只用 FreeRTOS 的程序不链入 wifi 组归档、堆函数来自 SDK；Wi-Fi 载体链入了 SDK 原生链接中所有有内容的包内成员。
- 镜像后处理：使用 manifest 中 `post_process` 指定的 boot2、分区表和 dts。

## 未完成

- 预编译无线库、工具链和烧录工具的再分发许可尚未核对，包只在本地使用。
- 包归档和包索引条目尚未生成。
- 包的运行期行为尚未在硬件上验证。

本地共享入口：[底层包约定](../docs/development/dependency-release.md)、[开发规范](../AGENTS.md)、[待决事项](../docs/project/open-decisions.md)。
