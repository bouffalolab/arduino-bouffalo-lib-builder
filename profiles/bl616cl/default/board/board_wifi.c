/*
 * Wi-Fi IRQ hookup moved from SDK bl616cldk board initialization. Keep it in a separate object so
 * interrupt0_handler does not pull wireless libraries into sketches that do not use Wi-Fi; the
 * package retains this record only through the Wi-Fi link group.
 */
#include "bflb_irq.h"
#include "board.h"
#include "bl616cl_glb.h"
#include "sysinit.h"

/**
 * @brief SYSINIT 30.01: route and enable the Wi-Fi interrupt after the interrupt controller
 * (30.00) and before SDK Wi-Fi bring-up (70.00).
 */
static void board_wifi_irq_sysinit(const void *arg)
{
    (void)arg;
    extern void interrupt0_handler(void);
    bflb_irq_attach(WIFI_IRQn, (irq_callback)interrupt0_handler, NULL);
    bflb_irq_enable(WIFI_IRQn);
}
SYSINIT(arduino_wifi_irq, SI_SUB_INTR, SI_ORDER_SECOND, board_wifi_irq_sysinit, NULL);
