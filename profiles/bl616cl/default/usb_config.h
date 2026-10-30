/*
 * CherryUSB configuration shipped together with libcherryusb.a; platform USB code must use the
 * same header. Enable CDC ACM and HID device classes, retaining other template defaults.
 */
#ifndef ARDUINO_BL_USB_CONFIG_H
#define ARDUINO_BL_USB_CONFIG_H

#include "cherryusb_config_template.h"

#undef CONFIG_USB_HS
#define CONFIG_USB_HS

#endif /* ARDUINO_BL_USB_CONFIG_H */
