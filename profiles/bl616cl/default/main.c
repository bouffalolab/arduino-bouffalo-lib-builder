/*
 * 载体工程入口：只为让 SDK 完成一次完整链接，验证本 profile 的库可以闭合，
 * 并产生 linker.ld 与链接参数。导出底层包时不包含本文件；
 * Arduino 固件的 main 由 Core 提供。
 * 初始化顺序与 SDK v2.3.37 examples/wifi/sta/wifi_tcp/main.c 相同，去掉了 shell；
 * Wi-Fi 中断改由 board_wifi_irq_init() 挂接（见 board_overlay.c）。
 */
#include "FreeRTOS.h"
#include "task.h"
#include <lwip/tcpip.h>
#include "wifi_mgmr_ext.h"
#include "fhost_api.h"
#include "wifi_mgmr.h"
#include "rfparam_adapter.h"
#include "async_event.h"
#include "board.h"
#include "usbd_core.h"
#include "usbd_cdc_acm.h"

extern void wifi_task_create(void);

static void wifi_event_handler(async_input_event_t ev, void *priv)
{
    if (ev->code == CODE_WIFI_ON_INIT_DONE) {
        wifi_mgmr_task_start();
    }
}

static void wifi_start_firmware_task(void *param)
{
    async_register_event_filter(EV_WIFI, wifi_event_handler, NULL);
    wifi_task_create();
    fhost_init();
    vTaskDelete(NULL);
}

int main(void)
{
    board_init();
    board_wifi_irq_init();

    if (rfparam_init(0, NULL, 0) != 0) {
        return 0;
    }

    tcpip_init(NULL, NULL);

    /* 引用 CherryUSB device 入口，确认 USB 库能链接；载体不初始化 USB。 */
    volatile void *usb_entry = (void *)usbd_initialize;
    (void)usb_entry;

    xTaskCreate(wifi_start_firmware_task, "wifi init", 1024, NULL, 10, NULL);

    vTaskStartScheduler();

    while (1) {
    }
}
