/* Wi-Fi 和 BLE 共享射频初始化；由应用任务串行调用，避免第二次校准正在工作的射频。 */
#include "board_overlay.h"
#include "rfparam_adapter.h"

int board_radio_init(void)
{
    static int result = 1;
    if (result == 1) {
        result = rfparam_init(0, NULL, 0);
    }
    return result;
}
