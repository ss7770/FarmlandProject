#include "Filter.h"
#include <stddef.h>

/* 限幅阈值：写死为 15 */
#define SOIL_FILTER_THRESHOLD  15

/* 去抖次数：连续超限 3 次才认为是干扰，超过 3 次承认新值 */
#define REJECT_COUNT_MAX       3

/* 滤波器状态（封装在内部，调用方无需关心） */
static uint16_t s_lastValue = 0;
static uint8_t  s_init = 1;
static uint8_t  s_rejectCount = 0;

void Filter_SensorData(SensorData_t *data)
{
    if (data == NULL) return;

    if (s_init) {
        // 首次运行，信任当前读数
        s_lastValue = data->soilHumidity;
        s_init = 0;
        s_rejectCount = 0;
        return;
    }

    // 计算差值（使用 int32_t 避免溢出，即使阈值很小也安全）
    int32_t diff = (int32_t)data->soilHumidity - s_lastValue;
    if (diff < 0) diff = -diff;

    if (diff > SOIL_FILTER_THRESHOLD) {
        // 超限
        s_rejectCount++;
        if (s_rejectCount >= REJECT_COUNT_MAX) {
            // 连续多次超限，认为是真实阶跃，更新有效值
            s_lastValue = data->soilHumidity;
            s_rejectCount = 0;
            // 此时 data->soilHumidity 保持原值（新值），无需修改
        } else {
            // 暂时认为是干扰，丢弃本次值，保持上次有效值
            data->soilHumidity = s_lastValue;
            // 可选日志：Serial2_Printf("Filter: rejected, keep %d\r\n", lastValue);
        }
    } else {
        // 正常波动，更新有效值并清零计数
        s_lastValue = data->soilHumidity;
        s_rejectCount = 0;
    }
}

void SoilFilter_Reset(void)
{
    s_init = 1;
    s_rejectCount = 0;
}
