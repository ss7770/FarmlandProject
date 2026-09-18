#ifndef __WATER_H
#define __WATER_H
#include "stm32f10x.h"
#include "Delay.h"
#include "ADCX.h"

void Water_Init(void);//初始化水位传感器
uint16_t Water_GetData(void);//获取水位ADC原始值
#endif
