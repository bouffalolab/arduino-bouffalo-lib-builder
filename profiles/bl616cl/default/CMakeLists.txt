cmake_minimum_required(VERSION 3.15)

find_package(bouffalo_sdk REQUIRED HINTS $ENV{BL_SDK_BASE})

# The SDK ships SNTP, but does not include it in the lwIP target by default.
target_sources(liblwip PRIVATE $ENV{BL_SDK_BASE}/components/net/lwip/lwip/src/apps/sntp/sntp.c)

# Exclude host paths from __FILE__/debug information by mapping build.sh's work root to /work.
# Map the SDK root to '.', matching its macro/debug prefix maps; later matching options take precedence.
if(DEFINED ENV{BL_PREFIX_MAP_ROOT})
    sdk_add_compile_options("-ffile-prefix-map=$ENV{BL_PREFIX_MAP_ROOT}=/work")
    sdk_add_compile_options("-ffile-prefix-map=$ENV{BL_SDK_BASE}=.")
endif()

# Profile configuration headers: FreeRTOSConfig.h, lwipopts_user.h, usb_config.h, mbedtls_sample_config.h and board_overlay.h.
sdk_add_include_directories(.)
sdk_add_include_directories(board)

# The blob carries LTO IR; GCC 10 resolves its internal calls before --wrap.
# Use its native objects so the hidden-beacon adapter is actually called.
sdk_add_link_options("-fno-lto" "-Wl,--wrap=wl80211_mac_start_ap" "-Wl,--wrap=build_ie_ssid")

target_sources(app PRIVATE board/board_overlay.c board/board_wifi.c board/board_radio.c board/board_time.c)

# Generate the TLS root bundle from every cacrt_all.pem certificate, without the SDK CSV filter; include it in libapp.a.
set(CRT_DIR $ENV{BL_SDK_BASE}/components/crypto/mbedtls/bl_crt_bundle)
set(CRT_BIN ${CMAKE_CURRENT_BINARY_DIR}/arduino_crt_bundle.bin)
add_custom_command(OUTPUT ${CRT_BIN}
    COMMAND python3 -I ${CRT_DIR}/gen_crt_bundle.py --input ${CRT_DIR}/cacrt_all.pem --output ${CRT_BIN} --quiet
    DEPENDS ${CRT_DIR}/gen_crt_bundle.py ${CRT_DIR}/cacrt_all.pem)
set_source_files_properties(arduino_crt_bundle.S PROPERTIES
    OBJECT_DEPENDS ${CRT_BIN} COMPILE_OPTIONS "-Wa,-I${CMAKE_CURRENT_BINARY_DIR}")
target_sources(app PRIVATE arduino_crt_bundle.S)

add_subdirectory(littlefs)

sdk_set_main_file(main.c)

project(arduino_bl_profile)
