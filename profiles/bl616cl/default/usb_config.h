/*
 * CherryUSB 配置。和 libcherryusb.a 一起打包，平台 USB 库必须使用同一份。
 * 本 profile 启用 device 侧 CDC ACM 与 HID；其余取 cherryusb_config_template.h 默认值。
 */
#ifndef ARDUINO_BL_USB_CONFIG_H
#define ARDUINO_BL_USB_CONFIG_H

#include "cherryusb_config_template.h"

#undef CONFIG_USB_HS
#define CONFIG_USB_HS

#endif /* ARDUINO_BL_USB_CONFIG_H */
