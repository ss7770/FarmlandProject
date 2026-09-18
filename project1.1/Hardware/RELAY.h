#ifndef __RELAY_H
#define __RELAY_H
#include "stm32f10x.h"

//继电器控制引脚定义（PB13）
#define RELAY_PIN       GPIO_Pin_13
#define RELAY_PORT      GPIOB
#define RELAY_CLOCK     RCC_APB2Periph_GPIOB

//继电器状态宏定义
#define RELAY_ON    GPIO_SetBits(RELAY_PORT, RELAY_PIN)//继电器吸合
#define RELAY_OFF   GPIO_ResetBits(RELAY_PORT, RELAY_PIN)//继电器断开

//函数声明
void Relay_Init(void);//继电器初始化
void Relay_Control(uint8_t flag);//继电器控制
#endif
