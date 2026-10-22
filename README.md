# arduino-bl-lib-builder

为 arduino-bl 构建和发布版本化底层依赖包。完整 SDK 的获取、配置和补丁只在此类维护者流程中处理。

当前阶段：能构建并导出 BL616CL 首个本地包 `bl616cl-libs`（profile `default`：FreeRTOS、lwIP、Wi-Fi STA（wl80211）、CherryUSB device CDC，BLE 关闭），以及镜像后处理与烧录工具包 `bl-flash-tools`，尚未发布。

## 使用

```sh
./build.sh                          # 获取固定版本的 SDK 与工具链，构建 profiles/bl616cl/default
python3 tools/export_package.py \
  --build work/build/bl616cl-default --build-log work/build/bl616cl-default.log \
  --sdk work/bouffalo_sdk --sources sources.json \
  --toolchain-bin work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  --profile bl616cl/default --version 0.3.0 --out work/out/bl616cl-libs
tools/smoke_test.sh work/out/bl616cl-libs \
  work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  work/build/bl616cl-default/build/build_out/arduino_bl_profile_bl616cl.elf \
  work/bouffalo_sdk work/build      # 之后的参数是测试时要隐藏的路径，原包目录会自动隐藏
python3 tools/test_export_package.py # 导出逻辑的单元测试；设置 BL_TEST_GCC 时额外用真实 gcc 检查
python3 tools/export_flash_tools.py \
  --sdk work/bouffalo_sdk --sources sources.json --chip bl616cl \
  --version 0.1.0 --out work/out/bl-flash-tools
python3 tools/test_export_flash_tools.py
tools/install_check.sh ../arduino-bl \
  work/bouffalo_sdk work/build      # 本地包索引安装链检查；之后的参数是要隐藏的路径
```

`sources.json` 固定 SDK（公网 bouffalo_sdk v2.3.37）和工具链（Xuantie GCC 10.2.0 V2.6.1）的来源与校验值。`work/` 存放检出、下载和构建输出，不入库。构建在 `work/build/` 中进行；构建前后 SDK 检出目录都必须没有任何改动或新增文件（含被忽略的文件），否则 `build.sh` 报错。导出时核对 SDK 检出与 `sources.json` 一致；`--out` 只会替换空目录或以前的导出。

## profile

`profiles/<chip>/<profile>/` 是一个 SDK 工程：

- `defconfig` 决定组件组合。Wi-Fi 使用 wl80211 方案，不用 FHOST。
- `FreeRTOSConfig.h`、`lwipopts_user.h`、`usb_config.h`、`mbedtls_sample_config.h` 会改变库内布局，随包导出，平台必须使用包内这一份。
- `board/` 通过 SDK 的 board overlay 机制替换 `board.c`，只保留芯片级初始化。控制台改由 `board_console_init()` 按需开启；Wi-Fi 中断由 `board_wifi_irq_init()` 挂接，与 `board_init()` 分开，避免不用 Wi-Fi 的固件链入无线库。
- `main.c` 只用于让 SDK 完成一次链接，不进入包；Wi-Fi 初始化顺序与 SDK 的 `examples/wifi/nethub` wl80211 分支相同。
- `link_groups.json` 定义链接组（目前只有 `wifi`），见下文“链接”。

## 包结构

`include/`、`lib/`、`ld/`、`boot/`、`flags/` 和 `manifest.json`，说明见 `tools/export_package.py` 开头。使用方的要点：

- 编译：`@flags/c_flags @flags/defines -iprefix <包>/include/ @flags/includes -include <包>/<manifest.forced_include>`。
- 链接：`@flags/ld_flags -T <包>/ld/linker.ld -L <包>/ld -L <包>/lib`，库放在 `--start-group @flags/ld_libs --end-group` 中；使用 Wi-Fi 的程序在组内再加 `@flags/ld_libs_wifi`。
- SDK 以 `--whole-archive` 链接全部组件库；包不这样做，以免每个程序都带入网络栈。只靠 `--whole-archive` 才生效的成员由导出脚本自动找出，打成 `lib/libforce_<组>.a`，由 `ld_libs`（base 组）或 `ld_libs_<组>` 整体链入。这类成员包括 SDK libc 移植层（覆盖 newlib 的 `malloc`、`printf` 等，否则堆会落到 newlib 的 sbrk 上）、FreeRTOS 的 `__errno` 与 pthread、flash ROM 补丁、Wi-Fi 监管域表和版本信息。清单见 manifest 的 `link.forced`。
- 冒烟测试用 `tools/check_link.py` 检查：只用 FreeRTOS 的程序不链入 wifi 组归档、堆函数来自 SDK；Wi-Fi 载体链入了 SDK 原生链接中所有有内容的包内成员。
- 镜像后处理：使用 manifest 中 `post_process` 指定的 boot2、分区表和 dts。

## 烧录工具包

`bl-flash-tools` 从同一 SDK 检出的 `tools/bflb_tools` 原样复制 Linux x86_64 的 `bflb_fw_post_proc` 和 `BLFlashCommand`，以及所选芯片的烧录配置，结构见 `tools/export_flash_tools.py` 开头。`BLFlashCommand` 每次运行都会在自身目录下创建 `log/`，目录不可写时直接退出，所以安装目录必须可写；`bflb_fw_post_proc` 只写镜像所在目录。

## 本地包索引

`tools/make_index.py` 把平台仓库 HEAD、`bl616cl-libs`、`bl-flash-tools` 和 `sources.json` 固定的工具链打成归档，生成 `package_bouffalolab_index.json`（只有 Linux x86_64 条目）。归档按名称排序、时间与属主清零，同样输入得到同样的 SHA-256。

`tools/install_check.sh` 用它在临时目录验证安装链：经 127.0.0.1 的 HTTP 服务（arduino-cli 接受 `file://` 索引，不接受 `file://` 归档）安装到空目录，然后在 bubblewrap 中断网、隐藏 `work/out`、`work/toolchain` 和给出的路径，依次检查：安装后编译、移动数据目录后编译、`packages/` 只读时编译、在 sketchbook 中修改 Core 后编译且工具包文件不变。安装步骤需要联网下载 arduino-cli 自带工具；HTTP 端口默认 8765，可用 `BL_INDEX_PORT` 改变。

## 未完成

- 预编译无线库、工具链和烧录工具的再分发许可尚未核对，包只在本地使用。`tools/bflb_tools` 中的程序没有单独的许可声明。
- 烧录工具只有 Linux x86_64 版本。
- 包索引只在本地验证，尚无公网地址。
- 包的运行期行为尚未在硬件上验证。

本地共享入口：[底层包约定](../docs/development/dependency-release.md)、[开发规范](../AGENTS.md)、[待决事项](../docs/project/open-decisions.md)。
