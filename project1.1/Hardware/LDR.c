#include "LDR.h"	

/**
  *@brief  光敏传感器初始化
  *@param  无
  *@retval 无
  */
void LDR_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;//GPIO初始化结构体
	RCC_APB2PeriphClockCmd(LDR_GPIO_CLK, ENABLE);//打开ADC IO端口时钟
	GPIO_InitStructure.GPIO_Pin = LDR_GPIO_PIN;//配置ADC IO引脚
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AIN;//设置为模拟输入
	GPIO_Init(LDR_GPIO_PORT, &GPIO_InitStructure);//初始化ADC IO
}

/**
  *@brief  读取光敏传感器ADC值
  *@param  无
  *@retval ADC转换结果（12位，0~4095）
  */
uint16_t LDR_ADC_Read(void)
{
	//设置指定ADC的规则组通道，采样时间
	return ADCX_GetValue(LDR_ADC_CHANNEL, ADC_SampleTime_55Cycles5);//返回指定通道的ADC采样值
}

/**
  *@brief  多次采样取平均值
  *@param  无
  *@retval 多次采样平均值
  */
uint16_t LDR_Average_Data(void)
{
	uint32_t tempData = 0;//32位变量用于累加采样值
	uint8_t	i;//循环计数器
	
	//先丢弃一次采样，确保ADC通道稳定
	LDR_ADC_Read();
	
	for(i = 0; i < LDR_READ_TIMES; i++)//循环读取指定次数
	{
		tempData += LDR_ADC_Read();//累加ADC采样值
		Delay_ms(5);
	}
	
	tempData /= LDR_READ_TIMES;//计算平均值
	return (uint16_t)tempData;//返回平均值，强制转换为16位
}

/**
  *@brief  获取光照强度值（单位：Lux）
  *@param  无
  *@retval 光照强度值（0~999Lux）
  */
uint16_t LDR_LuxData(void)
{
	float voltage = 0;//电压变量	
	float R = 0;//光敏电阻阻值变量	
	uint16_t Lux = 0;//光照强度变量
	
	voltage = LDR_Average_Data();//获取ADC平均值
	voltage  = voltage / 4096 * 3.3f;//将ADC值转换为电压值，12位ADC最大值4096，参考电压3.3V
	R = voltage / (3.3f - voltage) * 10000;//根据分压电路计算光敏电阻阻值，固定电阻为10kΩ
	Lux = 40000 * pow(R, -0.6021);//根据电阻-照度特性公式计算光照强度，经验公式系数
	
	if(Lux > 999)//限制最大输出值
	{
		Lux = 999;
	}
	return Lux;//返回光照强度值
}
