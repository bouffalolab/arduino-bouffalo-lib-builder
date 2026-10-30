#pragma once

#include <stdint.h>

/* All SDK cacrt_all.pem certificates in bl_crt_bundle format, passed to bl_crt_bundle_set(). */
extern const uint8_t arduino_crt_bundle[];
extern const uint32_t arduino_crt_bundle_size;
