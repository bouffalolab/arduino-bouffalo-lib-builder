#include <sys/time.h>

/**
 * @brief Store synchronized epoch seconds through the platform time hook.
 */
void board_sntp_set_time(unsigned int seconds)
{
    struct timeval now = { .tv_sec = seconds, .tv_usec = 0 };
    settimeofday(&now, NULL);
}
