#ifndef __FILTER_H
#define __FILTER_H

#include <stdint.h>

/* 传感器数据结构体（完整定义，与 main.c 原始定义一致） */
typedef struct {
    uint8_t  temp;          // 温度
    uint8_t  humi;          // 湿度
    uint16_t lightLux;      // 光照强度
    uint16_t soilHumidity;  // 土壤湿度
    uint16_t waterLevel;    // 水位
} SensorData_t;

/**
 * @brief 土壤湿度限幅滤波（带去抖）
 * @param data 传感器数据指针，滤波后的值会写回 data->soilHumidity
 */
void Filter_SensorData(SensorData_t *data);

/**
 * @brief 重置滤波器状态（如需要重新自适应）
 */
void SoilFilter_Reset(void);

#endif
