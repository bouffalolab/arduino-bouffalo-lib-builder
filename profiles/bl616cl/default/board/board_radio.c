/*
 * Share Wi-Fi/BLE radio initialization in a serialized application task, avoiding recalibration
 * while another radio user is active.
 */
#include "board_overlay.h"
#include "rfparam_adapter.h"

/**
 * @brief Initialize shared radio resources once for Wi-Fi and the BLE Controller.
 */
int board_radio_init(void)
{
    static int result = 1;
    if (result == 1) {
        result = rfparam_init(0, NULL, 0);
    }
    return result;
}
