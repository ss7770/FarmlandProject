#ifndef _ADCX_H_
#define _ADCX_H_
#include "stm32f10x.h"

//ADC选择
#define ADCX        ADC1
#define ADCX_CLK    RCC_APB2Periph_ADC1

//ADC通道宏定义
#define ADCX_CHANNEL_LDR      ADC_Channel_4//PA4 光敏传感器
#define ADCX_CHANNEL_TS       ADC_Channel_1//PA1 土壤湿度传感器
#define ADCX_CHANNEL_WATER    ADC_Channel_0//PA0 水位传感器

void ADCX_Init(void);//ADC初始化
uint16_t ADCX_GetValue(uint8_t ADC_Channel, uint8_t ADC_SampleTime);//获取ADC转换值
uint16_t ADCX_GetAverage(uint8_t ADC_Channel, uint8_t ADC_SampleTime, uint8_t Times);//获取多次ADC采样平均值
uint16_t ADCX_GetFilteredValue(uint8_t ADC_Channel, uint8_t ADC_SampleTime, uint8_t filterTimes);//获取滤波后的ADC值
#endif
