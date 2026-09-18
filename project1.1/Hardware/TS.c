#include "TS.h"	

/**
*@brief  土壤湿度传感器初始化
*@param  无
*@retval 无
*/
void TS_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;//GPIO初始化结构体
	RCC_APB2PeriphClockCmd(TS_GPIO_CLK, ENABLE);//打开ADC IO端口时钟
	GPIO_InitStructure.GPIO_Pin = TS_GPIO_PIN;//配置ADC IO引脚
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AIN;//设置为模拟输入
	GPIO_Init(TS_GPIO_PORT, &GPIO_InitStructure);//初始化ADC IO
}

/**
*@brief  读取土壤湿度ADC值
*@param  无
*@retval ADC转换结果(12位，0~4095)
*/
static uint16_t TS_ADC_Read(void)
{
	//设置指定ADC的规则组通道，采样时间
	return ADCX_GetValue(TS_ADC_CHANNEL, ADC_SampleTime_55Cycles5);
}

/**
*@brief  获取土壤湿度百分比值
*@param  无
*@retval 土壤湿度百分比（0~100%），数值越大表示湿度越高
*@note   公式：湿度(%) = 100 - (ADC值/40.96)
*        当ADC值为0时，湿度为100%；当ADC值为4095时，湿度为0%
*/
uint16_t TS_GetData(void)
{
	uint32_t tempData = 0;//累加变量
	uint8_t i;//循环变量
	
	//先丢弃一次采样，确保ADC通道稳定
	TS_ADC_Read();
	
	//多次采样取平均值，提高稳定性
	for(i = 0; i < TS_READ_TIMES; i++)
	{
		tempData += TS_ADC_Read();//累加ADC采样值
		Delay_ms(5);//采样间隔延时5ms
	}
	tempData /= TS_READ_TIMES;//计算平均值
	
	//ADC值范围0~4095，对应湿度100%~0%
	//公式：湿度 = 100 - (ADC值 / 40.96)
	return 100 - (float)tempData / 40.96;
}
