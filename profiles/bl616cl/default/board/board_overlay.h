/*
 * Derived from Apache-2.0 bouffalo_sdk v2.3.37 (f924fbb), bsp/board/bl616cldk/board.h. The SDK
 * uses these declarations and board_overlay.c instead of its default board files.
 */
#ifndef _BOARD_OVERLAY_H
#define _BOARD_OVERLAY_H

#include <stdint.h>
#include "bflb_rtc.h"
#include "board_gpio.h"
#include "board_flash_psram.h"

#define DEFAULT_ACOMP_VREF_1V65 33

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Package startup contract. Board initialization runs as SYSINIT records in board_overlay.c and
 * board_wifi.c (Wi-Fi link group only); after the heap and before C++ constructors the package
 * calls arduino_bl_core_preinit(), which the Arduino Core must define. SYSINIT then starts the
 * scheduler and calls main() from its task. There is no board_init() entry point.
 */
#define ARDUINO_BL_SYSINIT_STARTUP 1

/**
 * @brief Core pre-initialization run once from SYSINIT 40.04, before C++ constructors; defined
 * by the Arduino Core.
 */
void arduino_bl_core_preinit(void);

/**
 * @brief Route SDK printf/LOG_x to uart0, uart1 or uart2. Require distinct valid chip pins and
 * nonzero baudrate. Return zero on success; invalid arguments return -1 without touching
 * peripherals.
 */
int board_console_init(const char *uart_name, uint8_t tx_pin, uint8_t rx_pin, uint32_t baudrate);

/**
 * @brief Return the board profile's configured 32 kHz RTC source.
 */
enum bflb_rtc_32k_clk_type board_get_rtc_32k_clk_type(void);

/**
 * @brief Initialize the optional board analog-comparator configuration.
 */
void board_acomp_init(void);

#ifdef __cplusplus
}
#endif

#endif /* _BOARD_OVERLAY_H */
