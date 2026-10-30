# arduino-bl-lib-builder

为 arduino-bl 构建和发布版本化底层依赖包。完整 SDK 的获取、配置和补丁只在此类维护者流程中处理。

当前阶段：能构建并导出 BL616CL 本地包 `bl616cl-libs`（profile `default`：FreeRTOS、lwIP、Wi-Fi STA/AP（wl80211）、CherryUSB device CDC/HID、可选external-host BLE UART HCI Controller），以及镜像后处理与烧录工具包 `bl-flash-tools`，尚未公开发布。

## 使用

```sh
./build.sh                          # 获取固定版本的 SDK 与工具链，构建 profiles/bl616cl/default
python3 tools/export_package.py \
  --build work/build/bl616cl-default --build-log work/build/bl616cl-default.log \
  --sdk work/bouffalo_sdk --sdk-tree work/build/bl616cl-default.sdk --sources sources.json \
  --toolchain-bin work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  --profile bl616cl/default --version 0.9.0 --out work/out/bl616cl-libs
tools/smoke_test.sh work/out/bl616cl-libs \
  work/toolchain/xuantie-gcc-riscv-v2.6.1-linux-x86_64/bin \
  work/build/bl616cl-default/build/build_out/arduino_bl_profile_bl616cl.elf \
  work/bouffalo_sdk work/build      # 之后的参数是测试时要隐藏的路径，原包目录会自动隐藏
python3 tools/test_export_package.py # 导出逻辑的单元测试；设置 BL_TEST_GCC 时额外用真实 gcc 检查
python3 tools/test_prepare_sdk_tree.py
tools/build_layout_tool.sh work/layout-tool # Python 3.9.17, pinned requirements
python3 tools/export_flash_tools.py \
  --sdk work/bouffalo_sdk --sources sources.json --chip bl616cl \
  --version 0.2.0 --layout-tool work/layout-tool/dist/bl-layout \
  --out work/out/bl-flash-tools
python3 tools/test_export_flash_tools.py
tools/install_check.sh ../arduino-bl \
  work/bouffalo_sdk work/build      # 本地包索引安装链检查；之后的参数是要隐藏的路径
```

`sources.json` 固定 SDK（公网 bouffalo_sdk master 提交 `1cd1fd51`，即 v2.3.37 之后 3 个提交，含 wl80211 预编译库的二进制兼容修复，没有它连接 WPA3 AP 会崩溃；包含该修复的正式版本发布后改回按标签固定）和工具链（Xuantie GCC 10.2.0 V2.6.1）的来源与校验值。`work/` 存放检出、下载和构建输出，不入库。构建在 `work/build/` 中进行；构建前后 SDK 检出目录都必须没有任何改动或新增文件（含被忽略的文件），否则 `build.sh` 报错。导出时核对 SDK 检出与 `sources.json` 一致；`--out` 只会替换空目录或以前的导出。

包内不留构建机路径：profile 把 work 目录映射为 `/work`、SDK 目录映射为 `.`（`-ffile-prefix-map`，work 目录由 `build.sh` 经 `BL_PREFIX_MAP_ROOT` 传入）；导出时把 `autoconfig.h` 中的 SDK 路径改为相对路径，并检查导出的全部文件（含 `.a`），含构建机路径即失败。导出还会用 `strip -D --strip-debug` 剥离库的调试信息（lib 约 40 MB 降到 27 MB，符号与 LTO 段保留，链接出的镜像不变）。

## SDK 补丁

构建不直接使用检出目录：`tools/prepare_sdk_tree.py` 把检出中的已跟踪文件硬链接到 `work/build/<profile>.sdk`，再按 `sources.json` 的 `sdk.patches` 顺序打补丁（核对 sha256）；被补丁改动的文件先换成独立拷贝，检出目录保持不变。SDK 构建时用 git 生成版本信息，`build.sh` 让它读检出的仓库，打过补丁的库中版本带 dirty 标记（如 lhal `1cd1fd51-dirty`；浅检出没有标签，版本只含提交号）。导出用 `--sdk-tree` 指向这份目录，核对其中记录的提交与补丁和 `sources.json` 一致；补丁清单随 `sdk` 写入包的 manifest。

补丁放在 `patches/sdk/`，路径相对 SDK 根目录（`-p1`），文件开头写明问题、证据、上游提交和删除条件。修复先提交到上游，合入主线后随 bouffalo_sdk 公网版本发布；`sources.json` 升级到包含它的版本时删除对应补丁。

| 补丁 | 内容 | 上游 |
| --- | --- | --- |
| `lhal-usb-ep0-stall-cx-done.patch` | USB 设备驱动 stall EP0 时在同一次寄存器写中置 `USB_CX_STL` 与 `USB_CX_DONE`；否则 OUT 控制请求被拒后 EP0 不再响应，直到总线复位 | Gerrit lhal 11805 |

## 构建配置（profile）

`profiles/<chip>/<profile>/` 是一个 SDK 工程：

- `defconfig` 决定组件组合。Wi-Fi 使用 wl80211 方案，不用 FHOST；组件与 Wi-Fi 配置（含 `FreeRTOSConfig.h`、`lwipopts_user.h`）沿用 SDK `examples/wifi/macsw_bare`，只去掉 SHELL、IPERF。
- mbedTLS 是 SDK 的 3.6 版，生效的配置是 `config-tls-generic.h`（`flags/defines` 中的 `MBEDTLS_CONFIG_FILE`），选项取 defconfig 的 `CONFIG_MBEDTLS_*`。profile 里随 macsw_bare 带来的 `mbedtls_sample_config.h` 只在 `CONFIG_MBEDTLS_V2` 时使用，本 profile 不生效。
- TLS（ticket 0029）在 defconfig 另开 P-384、P-521、X25519 与证书束组件 `bl_crt_bundle`。构建时用 SDK 的 `gen_crt_bundle.py` 把 `cacrt_all.pem`（147 张）转成证书束，由 `arduino_crt_bundle.S` 编进 `libapp.a`，符号 `arduino_crt_bundle`、`arduino_crt_bundle_size` 声明在随包导出的 `arduino_crt_bundle.h`。证书束约 66 KB，只有用到它的固件链入；新增曲线使链入 Wi-Fi 的固件多约 14 KB flash（WPA3 SAE 加载曲线时引用全部已开曲线），RAM 不变。
- `FreeRTOSConfig.h`、`lwipopts_user.h`、`usb_config.h`、`mbedtls_sample_config.h` 会改变库内布局，随包导出，平台必须使用包内这一份。
- 0.4.1 起包含 CherryUSB HID 类与头文件，供平台的可选 raw HID 接口使用；是否枚举 HID 由应用注册决定，普通 sketch 仍只有 CDC。CMSIS-DAP 与 RA4M1 引脚控制不进入底层包。
- 0.5.0起使用同一固定GitHub SDK提供的`libbtblecontroller_bl616cl_uarthci.a`及匹配头文件，原归档SHA-256 `307b72a88fd2a1a9875b283de959972a53851d0085079fa2c5d69c60e0128bab`，库字符串1.6.212。`CONFIG_BLE_HOST_DISABLE=y`；上游预设固定EM=32 KB、2连接、4个ACL TX buffer、7个activity、CIS/BIS开、CTE关，这些编译选项不构成功能实测声明。`manifest.controller`记录原归档与导出库/配置入口；导出剥离调试后的归档哈希另列在`files`。
- ABI与其他包内库一致：ELF32 little-endian RISC-V、`rv32imafc_xtheade`、`ilp32f`、`-fshort-enums`，Xuantie GCC 10.2.0 V2.6.1。全profile保留32 KB EM，普通固件也承担这项预留；不用BLE时不链入Controller成员或启动任务。BLE Host/AT/板卡GPIO不进入包。Wi-Fi与BLE通过`board_radio_init()`共享一次射频初始化。
- `board/` 通过 SDK 的 board overlay 机制替换 `board.c`，只保留芯片级初始化。控制台改由 `board_console_init()` 按需开启；Wi-Fi 中断由 `board_wifi_irq_init()` 挂接，与 `board_init()` 分开，避免不用 Wi-Fi 的固件链入无线库。
- `main.c` 只用于让 SDK 完成一次链接，不进入包；Wi-Fi 初始化顺序与 SDK 的 `examples/wifi/macsw_bare` 相同。
- `link_groups.json` 定义可选的`wifi`和`ble`链接组，见下文“链接”；BLEController库通过`ld_libs_ble`启用BLE组。

## 包结构

`include/`、`lib/`、`ld/`、`boot/`、`flags/` 和 `manifest.json`，说明见 `tools/export_package.py` 开头。使用方的要点：

- 编译：`@flags/c_flags @flags/defines -iprefix <包>/include/ @flags/includes -include <包>/<manifest.forced_include>`。
- 链接：`@flags/ld_flags -T <包>/ld/linker.ld -L <包>/ld -L <包>/lib`，库放在 `--start-group @flags/ld_libs --end-group` 中；使用 Wi-Fi 的程序在组内再加 `@flags/ld_libs_wifi`。
- SDK 以 `--whole-archive` 链接全部组件库；包不这样做，以免每个程序都带入网络栈。只靠 `--whole-archive` 才生效的成员由导出脚本自动找出，打成 `lib/libforce_<组>.a`，由 `ld_libs`（base 组）或 `ld_libs_<组>` 整体链入。这类成员包括 SDK libc 移植层（覆盖 newlib 的 `malloc`、`printf` 等，否则堆会落到 newlib 的 sbrk 上）、FreeRTOS 的 `__errno` 与 pthread、flash ROM 补丁、Wi-Fi 监管域表和版本信息。清单见 manifest 的 `link.forced`。
- 冒烟测试用 `tools/check_link.py` 检查：只用 FreeRTOS 的程序不链入 wifi 组归档、堆函数来自 SDK；Wi-Fi 载体链入了 SDK 原生链接中所有有内容的包内成员。
- 镜像后处理：使用 manifest 中 `post_process` 指定的 boot2、分区表和 dts。

## 烧录工具包

`bl-flash-tools` 从同一 SDK 检出的 `tools/bflb_tools` 原样复制 Linux x86_64 的 `bflb_fw_post_proc` 和 `BLFlashCommand`，以及所选芯片的烧录配置，结构见 `tools/export_flash_tools.py` 开头。`BLFlashCommand` 会改写自身目录；0.2.0 的 `bl-layout` 在构建目录创建运行副本，安装目录可只读；`bflb_fw_post_proc` 只写镜像所在目录。

## 本地包索引

`tools/make_index.py` 把平台仓库 HEAD、`bl616cl-libs`、`bl-flash-tools` 和 `sources.json` 固定的工具链打成归档，生成 `package_bouffalolab_index.json`（只有 Linux x86_64 条目）。归档按名称排序、时间与属主清零，同样输入得到同样的 SHA-256。

`tools/install_check.sh` 用它在临时目录验证安装链：经 127.0.0.1 的 HTTP 服务（arduino-cli 接受 `file://` 索引，不接受 `file://` 归档）安装到空目录，然后在 bubblewrap 中断网、隐藏 `work/out`、`work/toolchain` 和给出的路径，依次检查：安装后编译、移动数据目录后编译、`packages/` 只读时编译、在 sketchbook 中修改 Core 后编译且工具包文件不变。安装步骤需要联网下载 arduino-cli 自带工具；HTTP 端口默认 8765，可用 `BL_INDEX_PORT` 改变。

## 未完成

- ARDUINO-54 已按用户确认的 Bouffalo Lab 提供许可口径收口厂家无线库、工具链和工具；内嵌第三方工具按用户要求不继续展开审核。该范围确认不等同公开发布批准，候选仍仅供本地评审。
- 烧录工具只有 Linux x86_64 版本。
- 包索引只在本地验证，尚无公网地址。
- 0.5.0的BLE HCI/ACL/关闭重启与Wi-Fi TCP/USB并发有Linux + 1V0实机记录，见[ARDUINO-43任务](../.tasks/2026-10-26-arduino-43-ble-controller/STATUS.md)；ARDUINO-44 后续完成7个官方示例和公共地址/RAM密钥配对加密，5例缺配件未验收；ARDUINO-53 已完成截至16:00的统一候选回归及BlueZ显式RA补丁重连。跨平台、RPA、掉电密钥保存仍未验收。软件构建、导出及安装检查不计入实机通过项。

本地共享入口：[底层包约定](../docs/development/dependency-release.md)、[开发规范](../AGENTS.md)、[待决事项](../docs/project/open-decisions.md)。

## 程序 Flash 存储（ARDUINO-46）

默认 profile 只导出固定 SDK 中的 littlefs 2.11 引擎（磁盘格式 2.1），
不包含 SDK 自动格式化、KV 或 libc/VFS 适配器。
`LICENSE.littlefs` 和引擎／布局身份记录在包 manifest 中。
sketch 正常消费该归档，Arduino 平台不会调用 SDK 构建。

`partition_cfg_4M_lfs_v1.toml` 为 littlefs 预留 `[0x310000,0x378000)`，共 416 KiB。
FW0 `[0x10000,0x190000)` 与 FW1 `[0x190000,0x310000)` 各为 1.5 MiB。
`mfg` 保留原命名，与 FW1 地址和大小相同：出厂前用于产测固件，出厂后作为 OTA B 分区。
media、参数区和 factory 地址保持不变。
文件系统的物理分区名为 `lfs`，厂家镜像工具会拒绝超过 7 个 ASCII 字符的分区名。
运行时从当前分区表读取几何信息，不依赖固定地址头文件。
SDK 的 `CONFIG_BFLB_MTD` 组件也管理 `bflb_boot2.c`，因此启用它来解析分区；
文件系统 I/O 直接调用 Flash，不经过 MTD 读写封装。

ROM 链接长度扣除了 4 KiB 固件头和 6 KiB ROM 头，超限固件在上传前即链接失败。
普通上传按区段擦除，不含文件系统镜像。
布局变更或上传旧布局固件前，需要显式离线备份和迁移；
不提供自动迁移或静默格式化。
运行时 API 和验收边界见平台的 `libraries/LittleFS/README.md`。

`bl-flash-tools 0.2.0` 包含独立的 `bl-layout`，
用于 Arduino 的 prebuild、后处理、advanced size、上传和导出配方。
维护者使用 Python 3.9.17 和 `tools/layout-tool-requirements.txt` 固定其构建环境：

```sh
# Maintenance host only; ordinary Arduino users need neither Python nor SDK.
python3 -m pip install -r tools/layout-tool-requirements.txt
tools/build_layout_tool.sh work/layout-tool
python3 tools/export_flash_tools.py --sdk work/bouffalo_sdk --sources sources.json \
  --chip bl616cl --version 0.2.0 --layout-tool work/layout-tool/dist/bl-layout \
  --out work/out/bl-flash-tools-0.2.0
python3 tools/test_bl_layout.py
```

可执行文件内置解释器和解析器，运行时许可随包导出。
工具保存选定的 TOML 及其哈希，为每次构建推导 ROM 容量限制，
检查后处理后的镜像大小，并在上传前绑定镜像／分区表哈希。
必须生成新的 `partition.bin`；厂家工具即使返回零，
只要没有生成新表，也不能静默复用旧表。
Arduino CLI 导出的配套文件和 `bl-flash/` 交付包为独立上传保留布局身份。
FlashCommand 的运行副本位于可写的构建目录，安装的工具保持不变。
用户的 Default/Custom/显式路径用法及布局迁移限制见平台 LittleFS README。

## 0.7.0 网络配置

profile 启用 IPv6，版本化补丁让 wl80211 的 IPv6 输出按 `LWIP_IPV6` 配置，不再依赖 fhost 专用的 `CFG_IPV6`。隐藏 AP 补丁在固定二进制构造 beacon 时输出空 SSID IE，定向 probe response 保留名称；启动后恢复其隐藏位以拒绝 wildcard probe。适配位于 SDK 侧，平台/bridge 不引用二进制内部符号。

原厂库含 LTO IR；GCC 10 对库内部调用优化会绕过 GNU `--wrap`。profile 用 `-fno-lto` 选用原厂 fat object 中现有 native 指令，并按 `sources.json` 固定 SDK 与补丁 SHA；没有改写二进制指令。Arduino 普通构建仍只消费导出包。13 个普通 sketch 的编译/map/镜像检查已验证未使用 WiFi 的程序不会带入 WiFi 组；这些是辅助检查，实机结论以 ARDUINO-49 记录为准。

## 0.8.0 时间与主机名

profile 将固定 SDK 的 `sntp.c` 编入 `liblwip.a`，`LWIP_NETIF_HOSTNAME=1` 改变 netif ABI，必须完整导出并与平台一起更新。SNTP 通过 `board_time.c` 调用 newlib `settimeofday`。`newlib-mtimer-wall-clock.patch` 修正原 setter 把 RTC 闹钟 API 当计数器设时的问题，并使用同 Core 的系统微秒计时器推进 UTC，避免本板 RC32K 在实机离线时的明显漂移。补丁 SHA 固定于 `sources.json`；不重置 RTC、不改闹钟、不启用 TLS 证书日期检查。

Arduino sketch 仍只消费预编译包，不运行 SDK 构建或修改安装目录。实机时钟、断网重同步、DHCP option 12 与 TLS 回归证据在 ARDUINO-45 task；构建、fake 和安装链仅为辅助检查。

## 0.9.0 IPv4 UDP 组播

profile启用`LWIP_IGMP=1`并完整重编导出。固定wl80211创建STA/AP两个带IGMP标志的netif；任意接口入组在两者各占一项。首轮实机复现默认8项IGMP池在第4个不同组时耗尽，现按现有8个netconn配置`MEMP_NUM_IGMP_GROUP = 2 * MEMP_NUM_NETCONN + 2`（18），含两个all-hosts项。没有增加socket上限或更改协议栈调度/分区/TLS策略；socket的入组登记和close自动退出仍使用SDK原有实现。

普通Arduino构建仅消费匹配预编译库及配置头，不编译SDK。真实RA WiFiS3/vela组播、离组循环、广播和UDP/TCP/TLS资源回收见ARDUINO-50任务；软件/fake、编译/安装链均仅作为辅助证据。
