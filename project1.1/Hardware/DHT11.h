#ifndef __DHT11_H
#define __DHT11_H
#include "stm32f10x.h"
#include <stdbool.h>

//DHT11引脚定义：PA11
#define DHT11_IO       GPIO_Pin_11
#define DHT11_GPIO     GPIOA
#define DHT11_RCC      RCC_APB2Periph_GPIOA

void DHT11_Init(void);//DHT11初始化
bool DHT11_ReadData(uint8_t *humi, uint8_t *temp);//读取温湿度数据
#endif
