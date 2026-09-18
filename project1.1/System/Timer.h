#ifndef __TIMER_H
#define __TIMER_H
#include "stm32f10x.h"

#define TIMER_MAX_CALLBACKS 10//最大回调函数数量
typedef void (*Timer_Callback_t)(void);//回调函数类型定义

void Timer_Init(void);//定时器初始化
uint32_t Timer_Get1msCount(void);//获取1ms计数器值
uint32_t Timer_GetTick(void);//获取系统运行时间（ms）
void Timer_DelayMs(uint32_t ms);//延时（ms）
uint8_t Timer_Register1msCallback(Timer_Callback_t Callback);//注册1ms周期回调函数
uint8_t Timer_Register10msCallback(Timer_Callback_t Callback);//注册10ms周期回调函数
uint8_t Timer_Register100msCallback(Timer_Callback_t Callback);//注册100ms周期回调函数
uint8_t Timer_Register1sCallback(Timer_Callback_t Callback);//注册1s周期回调函数
#endif
