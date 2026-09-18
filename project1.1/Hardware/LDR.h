#ifndef __LDR_H
#define __LDR_H
#include "stm32f10x.h"
#include "ADCX.h"
#include "Delay.h"
#include "math.h"

#define LDR_READ_TIMES	10//光照传感器ADC循环读取次数，用于平均滤波

//LDR引脚定义：PA4
#define	LDR_GPIO_CLK	RCC_APB2Periph_GPIOA
#define	LDR_GPIO_PORT	GPIOA
#define	LDR_GPIO_PIN	GPIO_Pin_4

//ADC通道宏定义
#define	LDR_ADC_CHANNEL	ADCX_CHANNEL_LDR

void LDR_Init(void);//光敏传感器初始化
uint16_t LDR_Average_Data(void);//多次采样取平均值
uint16_t LDR_LuxData(void);//获取光照强度值
#endif
