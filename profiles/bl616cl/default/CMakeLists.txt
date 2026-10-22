cmake_minimum_required(VERSION 3.15)

find_package(bouffalo_sdk REQUIRED HINTS $ENV{BL_SDK_BASE})

# 与 SDK wifi_tcp 示例一致：发送时允许 pbuf 链；wl80211 的 wl80211_output() 支持 pbuf 链
sdk_add_compile_definitions(-DHIGH_PERFORMANCE_PBUF=1)

# FreeRTOSConfig.h、lwipopts_user.h、usb_config.h、mbedtls_sample_config.h、board_overlay.h
sdk_add_include_directories(.)
sdk_add_include_directories(board)

target_sources(app PRIVATE board/board_overlay.c board/board_wifi.c)

sdk_set_main_file(main.c)

project(arduino_bl_profile)
