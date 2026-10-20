/* SPDX-License-Identifier: Apache-2.0 */
/* Monotonic seconds; RAM-only health lease, with an absolute trial deadline. */
static int watchdog_reason(long now, long start, long heartbeat, int healthy_seen) {
    if (now < start || now - start >= 3600) return 3;
    if (heartbeat > 0 && heartbeat <= now && now - heartbeat <= 120) return 0;
    if (healthy_seen) return 2;
    return now - start >= 600 ? 1 : 0;
}

/* Daily profile uses CLOCK_MONOTONIC for both ends of the lease. Time spent
 * suspended does not consume it. Only the absolute trial deadline is removed.
 */
static int watchdog_daily_reason(long now,long start,long heartbeat,int healthy_seen) {
    if(start<0 || now<start)return 4;
    if(heartbeat>0 && heartbeat<=now && now-heartbeat<=120)return 0;
    if(healthy_seen)return 2;
    return now-start>=600?1:0;
}
