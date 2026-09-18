#ifndef __TS_H
#define	__TS_H
#include "stm32f10x.h"
#include "ADCX.h"
#include "Delay.h"
#include "math.h"

#define TS_READ_TIMES	10//土壤湿度ADC循环读取次数

//TS引脚定义：PA1
#define	TS_GPIO_CLK		RCC_APB2Periph_GPIOA
#define	TS_GPIO_PORT	GPIOA
#define	TS_GPIO_PIN		GPIO_Pin_1

//ADC通道宏定义
#define	TS_ADC_CHANNEL	ADCX_CHANNEL_TS

void TS_Init(void);//土壤湿度传感器初始化
uint16_t TS_GetData(void);//获取土壤湿度百分比值
#endif
