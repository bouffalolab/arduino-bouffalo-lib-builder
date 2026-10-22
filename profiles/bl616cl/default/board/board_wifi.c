/*
 * Wi-Fi 中断挂接，原在 SDK bl616cldk/board.c 的 board_init() 中。
 * 单独成文件，使不使用 Wi-Fi 的固件不会因 interrupt0_handler 链入无线库。
 */
#include "bflb_irq.h"
#include "board.h"
#include "bl616cl_glb.h"

void board_wifi_irq_init(void)
{
    extern void interrupt0_handler(void);
    bflb_irq_attach(WIFI_IRQn, (irq_callback)interrupt0_handler, NULL);
    bflb_irq_enable(WIFI_IRQn);
}
