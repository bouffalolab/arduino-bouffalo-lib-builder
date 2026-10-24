#pragma once

#include <stdint.h>

/* TLS 根证书束：SDK cacrt_all.pem 的全部证书，bl_crt_bundle 格式，交给 bl_crt_bundle_set() */
extern const uint8_t arduino_crt_bundle[];
extern const uint32_t arduino_crt_bundle_size;
