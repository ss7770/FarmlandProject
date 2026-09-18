#include "water.h"

/**
  *@brief  初始化水位传感器
  *@param  无
  *@retval 无
  */
void Water_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;//GPIO初始化结构体
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);//打开ADC IO端口时钟
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_0;//配置ADC IO引脚
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AIN;//设置为模拟输入
	GPIO_Init(GPIOA, &GPIO_InitStructure);//初始化ADC IO
}

/**
  *@brief  获取水位传感器ADC值
  *@param  无
  *@retval 水位ADC原始值（0~4095）
  */
uint16_t Water_GetData(void)
{
    //使用统一ADC模块获取水位通道的平均值
    return ADCX_GetAverage(ADCX_CHANNEL_WATER, ADC_SampleTime_239Cycles5, 10);
}
