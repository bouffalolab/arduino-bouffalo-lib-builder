/*
 * SDK carrier entry point used only to close a complete profile link and capture linker.ld/flags.
 * Do not export this object; Arduino firmware gets main and arduino_bl_core_preinit from the Core.
 * SYSINIT owns board, C++ constructor, partition, RF and Wi-Fi startup and calls main() from its
 * task after starting the scheduler, so main() only references optional components and returns.
 */
#include "FreeRTOS.h"
#include "board.h"
#include "wifi_mgmr.h"
#include "bflb_boot2.h"
#include "usbd_core.h"
#include "btble_lib_api.h"

/**
 * @brief Carrier placeholder for the Core pre-initialization hook called from SYSINIT 40.04.
 */
void arduino_bl_core_preinit(void)
{
}

/**
 * @brief Link the dependency carrier for packaging checks; this SDK entry is not exported into
 * sketch startup.
 */
int main(void)
{
    btble_controller_init(configMAX_PRIORITIES - 2);

    /* Reference CherryUSB, Wi-Fi management and the partition table (pulling the on-demand boot2
     * record) to verify link closure without starting them. */
    void *volatile usb_entry = (void *)usbd_initialize;
    void *volatile wifi_entry = (void *)wifi_mgmr_sta_mac_get;
    void *volatile partition_entry = (void *)bflb_boot2_get_pt_config;
    (void)usb_entry;
    (void)wifi_entry;
    (void)partition_entry;
    return 0;
}
