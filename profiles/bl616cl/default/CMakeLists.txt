cmake_minimum_required(VERSION 3.15)

find_package(bouffalo_sdk REQUIRED HINTS $ENV{BL_SDK_BASE})

# 构建机路径不进入库（__FILE__ 与调试信息）：build.sh 传入 work 目录，映射为 /work；
# SDK 目录再映射为 .，与 SDK 自带的 -fmacro/-fdebug-prefix-map 结果相同（多个匹配时后出现者优先）
if(DEFINED ENV{BL_PREFIX_MAP_ROOT})
    sdk_add_compile_options("-ffile-prefix-map=$ENV{BL_PREFIX_MAP_ROOT}=/work")
    sdk_add_compile_options("-ffile-prefix-map=$ENV{BL_SDK_BASE}=.")
endif()

# FreeRTOSConfig.h、lwipopts_user.h、usb_config.h、mbedtls_sample_config.h、board_overlay.h
sdk_add_include_directories(.)
sdk_add_include_directories(board)

target_sources(app PRIVATE board/board_overlay.c board/board_wifi.c)

sdk_set_main_file(main.c)

project(arduino_bl_profile)
