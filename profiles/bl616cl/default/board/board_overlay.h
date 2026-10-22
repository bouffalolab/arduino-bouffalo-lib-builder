/*
 * 来源：bouffalo_sdk v2.3.37 (f924fbb) bsp/board/bl616cldk/board.h，Apache-2.0。
 * 存在本文件时，SDK 的 board.h 改用这里的声明，board.c 改由 board_overlay.c 提供。
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

/* 芯片级初始化：flash、时钟、中断、堆；不初始化控制台，不输出启动信息。 */
void board_init(void);

/*
 * 把 SDK 控制台（printf、LOG_x）接到指定 UART。
 * uart_name 为 "uart0"、"uart1" 或 "uart2"，tx_pin 与 rx_pin 须为芯片范围内的不同引脚，
 * baudrate 不为 0；成功返回 0，参数无效返回 -1，此时不改动任何外设。
 */
int board_console_init(const char *uart_name, uint8_t tx_pin, uint8_t rx_pin, uint32_t baudrate);

/* 挂接并使能 Wi-Fi 中断；启动 Wi-Fi 前调用一次。 */
void board_wifi_irq_init(void);

enum bflb_rtc_32k_clk_type board_get_rtc_32k_clk_type(void);

void board_acomp_init(void);

#ifdef __cplusplus
}
#endif

#endif /* _BOARD_OVERLAY_H */
