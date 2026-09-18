#include "ADCX.h"

/**
*@brief  ADC初始化
*@param  无
*@retval 无
*/
void ADCX_Init(void)
{
	//使能ADC时钟
	RCC_APB2PeriphClockCmd(ADCX_CLK, ENABLE);
	
	//ADC频率进行6分频
	RCC_ADCCLKConfig(RCC_PCLK2_Div6);
	
	//配置ADC结构体
	ADC_InitTypeDef ADC_InitStructure;
	ADC_InitStructure.ADC_Mode = ADC_Mode_Independent;//独立模式
	ADC_InitStructure.ADC_DataAlign = ADC_DataAlign_Right;//数据右对齐
	ADC_InitStructure.ADC_ExternalTrigConv = ADC_ExternalTrigConv_None;//软件触发
	ADC_InitStructure.ADC_ContinuousConvMode = DISABLE;//单次转换
	ADC_InitStructure.ADC_ScanConvMode = DISABLE;//非扫描模式
	ADC_InitStructure.ADC_NbrOfChannel = 1;//总通道数
	ADC_Init(ADCX, &ADC_InitStructure);//初始化ADC
	
	//使能ADCX
	ADC_Cmd(ADCX, ENABLE);
	
	//进行ADC校准
	ADC_ResetCalibration(ADCX);
	while(ADC_GetResetCalibrationStatus(ADCX) == SET);
	ADC_StartCalibration(ADCX);
	while(ADC_GetCalibrationStatus(ADCX) == SET);
}

/**
*@brief  获取ADC转换后的数据
*@param  ADC_Channel 选择需要采集的ADC通道
*@param  ADC_SampleTime 选择采样时间
*@retval 返回转换后的模拟信号数值
*@note   每次调用会切换通道并执行转换，支持多通道独立采集
*/
uint16_t ADCX_GetValue(uint8_t ADC_Channel, uint8_t ADC_SampleTime)
{   
	ADC_RegularChannelConfig(ADCX, ADC_Channel, 1, ADC_SampleTime);//配置指定ADC通道（每次调用重新配置，实现通道切换）
	ADC_SoftwareStartConvCmd(ADCX, ENABLE);//软件触发ADC转换
	while(ADC_GetFlagStatus(ADCX, ADC_FLAG_EOC) == RESET);//等待转换完成
	return ADC_GetConversionValue(ADCX);//返回转换值
}

/**
*@brief  获取多次ADC采样平均值
*@param  ADC_Channel 选择需要采集的ADC通道
*@param  ADC_SampleTime 选择采样时间
*@param  Times 采样次数
*@retval 返回多次采样的平均值
*/
uint16_t ADCX_GetAverage(uint8_t ADC_Channel, uint8_t ADC_SampleTime, uint8_t Times)
{
	uint32_t tempData = 0;//累加变量
	uint8_t i;//循环变量
	
	for(i = 0; i < Times; i++)
	{
		tempData += ADCX_GetValue(ADC_Channel, ADC_SampleTime);//累加ADC采样值
	}
	tempData /= Times;//计算平均值
	return (uint16_t)tempData;//返回平均值
}

/**
*@brief  获取指定通道的ADC原始值（带滤波）
*@param  ADC_Channel 选择需要采集的ADC通道
*@param  ADC_SampleTime 选择采样时间
*@param  filterTimes 滤波次数
*@retval 返回滤波后的ADC值
*/
uint16_t ADCX_GetFilteredValue(uint8_t ADC_Channel, uint8_t ADC_SampleTime, uint8_t filterTimes)
{
	uint32_t tempData = 0;//累加变量
	uint8_t i;//循环变量
	uint16_t samples[10];//采样数组
	uint8_t j, k;//排序变量
	uint16_t temp;//交换临时变量
	
	//采集指定次数的样本
	for(i = 0; i < filterTimes && i < 10; i++)
	{
		samples[i] = ADCX_GetValue(ADC_Channel, ADC_SampleTime);//获取采样值
	}
	
	//冒泡排序从小到大
	for(i = 0; i < filterTimes - 1; i++)
	{
		for(j = 0; j < filterTimes - 1 - i; j++)
		{
			if(samples[j] > samples[j + 1])
			{
				temp = samples[j];
				samples[j] = samples[j + 1];
				samples[j + 1] = temp;
			}
		}
	}
	
	//去掉最大最小值后取平均（中值平均滤波）
	for(i = 1; i < filterTimes - 1; i++)
	{
		tempData += samples[i];
	}
	tempData /= (filterTimes - 2);
	
	return (uint16_t)tempData;
}
