/*
 * Wi-Fi IRQ hookup moved from SDK bl616cldk board_init. Keep it in a separate object so
 * interrupt0_handler does not pull wireless libraries into sketches that do not use Wi-Fi.
 */
#include "bflb_irq.h"
#include "board.h"
#include "bl616cl_glb.h"

/**
 * @brief Route and enable the SDK Wi-Fi interrupt independently of BLE startup.
 */
void board_wifi_irq_init(void)
{
    extern void interrupt0_handler(void);
    bflb_irq_attach(WIFI_IRQn, (irq_callback)interrupt0_handler, NULL);
    bflb_irq_enable(WIFI_IRQn);
}
