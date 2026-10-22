# arduino-bl-lib-builder

为 arduino-bl 构建和发布版本化底层依赖包。完整 SDK 的获取、配置和补丁只在此类维护者流程中处理。

当前阶段：能构建并导出 BL616CL 首个本地包 `bl616cl-libs`（profile `default`：FreeRTOS、lwIP、Wi-Fi STA、CherryUSB device CDC，BLE 关闭），尚未发布。

## 使用

```sh
./build.sh                          # 获取固定版本的 SDK 与工具链，构建 profiles/bl616cl/default
python3 tools/export_package.py \
  --build work/build/bl616cl-default --build-log work/build/bl616cl-default.log \
  --sdk work/bouffalo_sdk --sources sources.json \
  --profile bl616cl/default --version 0.1.0 --out work/out/bl616cl-libs
tools/smoke_test.sh work/out/bl616cl-libs \
  work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  work/build/bl616cl-default/build/build_out/arduino_bl_profile_bl616cl.elf \
  work/bouffalo_sdk work/build      # 之后的参数是测试时要隐藏的路径
python3 tools/test_export_package.py # 导出逻辑的单元测试；设置 BL_TEST_GCC 时额外用真实 gcc 检查
```

`sources.json` 固定 SDK（公网 bouffalo_sdk v2.3.37）和工具链（Xuantie GCC 10.2.0 V2.6.1）的来源与校验值。`work/` 存放检出、下载和构建输出，不入库。构建在 `work/build/` 中进行；若构建改动了 SDK 检出目录，`build.sh` 会报错。

## profile

`profiles/<chip>/<profile>/` 是一个 SDK 工程：

- `defconfig` 决定组件组合。
- `FreeRTOSConfig.h`、`lwipopts_user.h`、`usb_config.h`、`mbedtls_sample_config.h` 会改变库内布局，随包导出，平台必须使用包内这一份。
- `board/` 通过 SDK 的 board overlay 机制替换 `board.c`，只保留芯片级初始化。控制台改由 `board_console_init()` 按需开启；Wi-Fi 中断由 `board_wifi_irq_init()` 挂接，与 `board_init()` 分开，避免不用 Wi-Fi 的固件链入无线库。
- `main.c` 只用于让 SDK 完成一次链接，不进入包。

## 包结构

`include/`、`lib/`、`ld/`、`boot/`、`flags/` 和 `manifest.json`，说明见 `tools/export_package.py` 开头。使用方的要点：

- 编译：`@flags/c_flags @flags/defines -iprefix <包>/include/ @flags/includes -include <包>/<manifest.forced_include>`。
- 链接：`@flags/ld_flags -T <包>/ld/linker.ld -L <包>/ld -L <包>/lib`，库放在 `--start-group @flags/ld_libs --end-group` 中。不使用 `--whole-archive`。
- 镜像后处理：使用 manifest 中 `post_process` 指定的 boot2、分区表和 dts。

## 未完成

- 预编译无线库、工具链和烧录工具的再分发许可尚未核对，包只在本地使用。
- 包归档和包索引条目尚未生成。
- builder 仓库尚未提交，manifest 中的 `builder.commit` 为空。

本地共享入口：[底层包约定](../docs/development/dependency-release.md)、[开发规范](../AGENTS.md)、[待决事项](../docs/project/open-decisions.md)。
