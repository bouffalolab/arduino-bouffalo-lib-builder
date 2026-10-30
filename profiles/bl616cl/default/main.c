/*
 * SDK carrier entry point used only to close a complete profile link and capture linker.ld/flags.
 * Do not export this object; Arduino firmware gets main from the Core. Follow macsw_bare wl80211
 * initialization without the shell; board_wifi_irq_init owns Wi-Fi IRQ hookup.
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
#include "btble_lib_api.h"

/**
 * @brief Drain deferred radio events while preserving the SDK scheduler exclusion convention.
 */
static void async_event_handler(void *arg1, uint32_t arg2)
{
    vTaskSuspendAll();
    async_event_loop();
    xTaskResumeAll();
}

/**
 * @brief Schedule deferred async-event processing with a timer-task-safe wait policy.
 */
static void async_event_loop_wake(void)
{
    TickType_t wait = portMAX_DELAY;

    if (xTimerGetTimerDaemonTaskHandle() == xTaskGetCurrentTaskHandle()) {
        wait = 0;
    }
    xTimerPendFunctionCall(async_event_handler, NULL, 0, wait);
}

/**
 * @brief Start Wi-Fi firmware, lwIP and management in their required order, then delete this
 * startup task.
 */
static void wifi_start_firmware_task(void *param)
{
    wifi_task_create();
    tcpip_init(NULL, NULL);
    wl80211_init();
    wifi_mgmr_init();
    vTaskDelete(NULL);
}

/**
 * @brief Link the dependency carrier for packaging checks; this SDK entry is not exported into
 * sketch startup.
 */
int main(void)
{
    board_init();
    board_wifi_irq_init();

    if (rfparam_init(0, NULL, 0) != 0) {
        return 0;
    }

    async_event_init(async_event_loop_wake);
    btble_controller_init(configMAX_PRIORITIES - 2);

    /* Reference CherryUSB to verify link closure without initializing USB in the carrier. */
    volatile void *usb_entry = (void *)usbd_initialize;
    (void)usb_entry;

    xTaskCreate(wifi_start_firmware_task, "wifi init", 1024, NULL, 10, NULL);

    vTaskStartScheduler();

    while (1) {
    }
}
