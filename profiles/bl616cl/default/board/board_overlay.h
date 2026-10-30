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

/**
 * @brief Initialize chip Flash, clocks, interrupts and heap without console setup or startup
 * output.
 */
void board_init(void);

/**
 * @brief Route SDK printf/LOG_x to uart0, uart1 or uart2. Require distinct valid chip pins and
 * nonzero baudrate. Return zero on success; invalid arguments return -1 without touching
 * peripherals.
 */
int board_console_init(const char *uart_name, uint8_t tx_pin, uint8_t rx_pin, uint32_t baudrate);

/**
 * @brief Attach/enable Wi-Fi interrupts once before starting Wi-Fi.
 */
void board_wifi_irq_init(void);

/**
 * @brief Shared Wi-Fi/BLE radio initialization, serialized by the application task. Initialize
 * RF parameters only once; return zero on success.
 */
int board_radio_init(void);

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
