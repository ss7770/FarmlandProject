#ifndef __BUZZER_H
#define __BUZZER_H
#include "stm32f10x.h"

//蜂鸣器引脚定义：PB12
#define BUZZER_GPIO_PORT	GPIOB
#define BUZZER_GPIO_PIN		GPIO_Pin_12
#define BUZZER_GPIO_CLK		RCC_APB2Periph_GPIOB

//默认阈值定义
#define BUZZER_TEMP_THRESHOLD_DEFAULT	30//温度阈值（摄氏度）
#define BUZZER_HUMI_THRESHOLD_DEFAULT	50//湿度阈值（百分比）
#define BUZZER_LIGHT_THRESHOLD_DEFAULT	800//光照阈值（Lux）
#define BUZZER_WATER_THRESHOLD_DEFAULT	1000//水位阈值（ADC原始值）

//全局阈值变量（可被修改）
extern uint8_t g_Buzzer_TempThreshold;//当前温度阈值
extern uint8_t g_Buzzer_HumiThreshold;//当前湿度阈值
extern uint16_t g_Buzzer_LightThreshold;//当前光照阈值
extern uint16_t g_Buzzer_WaterThreshold;//当前水位阈值

void Buzzer_Init(void);//初始化蜂鸣器
void Buzzer_On(void);//打开蜂鸣器
void Buzzer_Off(void);//关闭蜂鸣器
void Buzzer_ResetToDefault(void);//恢复默认阈值
void Buzzer_CheckAndAlert(uint8_t temp, uint8_t humi, uint16_t light, uint16_t water);//检查传感器并触发警报

//阈值设置函数
void Buzzer_SetTempThreshold(uint8_t threshold);//设置温度阈值
void Buzzer_SetHumiThreshold(uint8_t threshold);//设置湿度阈值
void Buzzer_SetLightThreshold(uint16_t threshold);//设置光照阈值
void Buzzer_SetWaterThreshold(uint16_t threshold);//设置水位阈值
uint8_t Buzzer_GetTempThreshold(void);//获取温度阈值
uint8_t Buzzer_GetHumiThreshold(void);//获取湿度阈值
uint16_t Buzzer_GetLightThreshold(void);//获取光照阈值
uint16_t Buzzer_GetWaterThreshold(void);//获取水位阈值
#endif
