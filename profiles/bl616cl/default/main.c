/*
 * 载体工程入口：只为让 SDK 完成一次完整链接，验证本 profile 的库可以闭合，
 * 并产生 linker.ld 与链接参数。导出底层包时不包含本文件；
 * Arduino 固件的 main 由 Core 提供。
 * Wi-Fi 使用 wl80211 方案，初始化顺序与 SDK examples/wifi/macsw_bare 相同，去掉了 shell；
 * Wi-Fi 中断改由 board_wifi_irq_init() 挂接（见 board_overlay.c）。
 */
#include "FreeRTOS.h"
#include "task.h"
#include "timers.h"
#include <lwip/tcpip.h>
#include "wl80211.h"
#include "wifi_mgmr.h"
#include "macsw_plat.h"
#include "rfparam_adapter.h"
#include "async_event.h"
#include "board.h"
#include "usbd_core.h"
#include "usbd_cdc_acm.h"

static void async_event_handler(void *arg1, uint32_t arg2)
{
    vTaskSuspendAll();
    async_event_loop();
    xTaskResumeAll();
}

static void async_event_loop_wake(void)
{
    TickType_t wait = portMAX_DELAY;

    if (xTimerGetTimerDaemonTaskHandle() == xTaskGetCurrentTaskHandle()) {
        wait = 0;
    }
    xTimerPendFunctionCall(async_event_handler, NULL, 0, wait);
}

static void wifi_start_firmware_task(void *param)
{
    wifi_task_create();
    tcpip_init(NULL, NULL);
    wl80211_init();
    wifi_mgmr_init();
    vTaskDelete(NULL);
}

int main(void)
{
    board_init();
    board_wifi_irq_init();

    if (rfparam_init(0, NULL, 0) != 0) {
        return 0;
    }

    async_event_init(async_event_loop_wake);

    /* 引用 CherryUSB device 入口，确认 USB 库能链接；载体不初始化 USB。 */
    volatile void *usb_entry = (void *)usbd_initialize;
    (void)usb_entry;

    xTaskCreate(wifi_start_firmware_task, "wifi init", 1024, NULL, 10, NULL);

    vTaskStartScheduler();

    while (1) {
    }
}
