#include "Buzzer.h"

//蜂鸣器状态标志
static uint8_t g_Buzzer_AlertFlag = 0;//警报标志，1表示正在报警

//全局阈值变量定义
uint8_t g_Buzzer_TempThreshold = BUZZER_TEMP_THRESHOLD_DEFAULT;//当前温度阈值
uint8_t g_Buzzer_HumiThreshold = BUZZER_HUMI_THRESHOLD_DEFAULT;//当前湿度阈值
uint16_t g_Buzzer_LightThreshold = BUZZER_LIGHT_THRESHOLD_DEFAULT;//当前光照阈值
uint16_t g_Buzzer_WaterThreshold = BUZZER_WATER_THRESHOLD_DEFAULT;//当前水位阈值

/**
*@brief  蜂鸣器GPIO初始化
*@param  无
*@retval 无
*/
void Buzzer_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;//定义GPIO初始化结构体

	//使能GPIO时钟
	RCC_APB2PeriphClockCmd(BUZZER_GPIO_CLK, ENABLE);//使能蜂鸣器引脚所在的GPIO时钟

	//配置蜂鸣器引脚为推挽输出
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;//推挽输出
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;//设置IO口速度为50MHz
	GPIO_InitStructure.GPIO_Pin = BUZZER_GPIO_PIN;//设置要初始化的引脚
	GPIO_Init(BUZZER_GPIO_PORT, &GPIO_InitStructure);//初始化GPIO

	//初始状态：关闭蜂鸣器
	GPIO_ResetBits(BUZZER_GPIO_PORT, BUZZER_GPIO_PIN);

	//初始化报警标志
	g_Buzzer_AlertFlag = 0;
}

/**
*@brief  打开蜂鸣器
*@param  无
*@retval 无
*/
void Buzzer_On(void)
{
	if(g_Buzzer_AlertFlag == 0)//如果当前没有报警
	{
		GPIO_SetBits(BUZZER_GPIO_PORT, BUZZER_GPIO_PIN);
		g_Buzzer_AlertFlag = 1;//设置报警标志
	}
}

/**
*@brief  关闭蜂鸣器（当所有条件都不满足时）
*@param  无
*@retval 无
*/
void Buzzer_Off(void)
{
	if(g_Buzzer_AlertFlag == 1)//如果正在报警
	{
		GPIO_ResetBits(BUZZER_GPIO_PORT, BUZZER_GPIO_PIN);
		g_Buzzer_AlertFlag = 0;//清除报警标志
	}
}

/**
*@brief  恢复默认阈值
*@param  无
*@retval 无
*/
void Buzzer_ResetToDefault(void)
{
	g_Buzzer_TempThreshold = BUZZER_TEMP_THRESHOLD_DEFAULT;//恢复温度默认阈值
	g_Buzzer_HumiThreshold = BUZZER_HUMI_THRESHOLD_DEFAULT;//恢复湿度默认阈值
	g_Buzzer_LightThreshold = BUZZER_LIGHT_THRESHOLD_DEFAULT;//恢复光照默认阈值
	g_Buzzer_WaterThreshold = BUZZER_WATER_THRESHOLD_DEFAULT;//恢复水位默认阈值
}

/**
*@brief  检查传感器并触发警报
*@param  temp 当前温度值
*@param  humi 当前湿度值
*@param  light 当前光照强度值
*@param  water 当前水位ADC值
*@retval 无
*@note   当温度超过阈值或湿度超过阈值或光照超过阈值或水位低于阈值时，蜂鸣器触发长响
*/
void Buzzer_CheckAndAlert(uint8_t temp, uint8_t humi, uint16_t light, uint16_t water)
{
    //判断是否触发警报条件：使用当前全局阈值变量
    if(temp > g_Buzzer_TempThreshold || humi > g_Buzzer_HumiThreshold || light > g_Buzzer_LightThreshold || water < g_Buzzer_WaterThreshold)
    {
        //条件满足，触发警报
        if(g_Buzzer_AlertFlag == 0)
        {
            GPIO_SetBits(BUZZER_GPIO_PORT, BUZZER_GPIO_PIN);
            g_Buzzer_AlertFlag = 1;
        }
    }
    else
    {
        //所有条件都不满足，关闭警报
        if(g_Buzzer_AlertFlag == 1)
        {
            GPIO_ResetBits(BUZZER_GPIO_PORT, BUZZER_GPIO_PIN);
            g_Buzzer_AlertFlag = 0;
        }
    }
}

/**
*@brief  设置温度阈值
*@param  threshold 新的温度阈值
*@retval 无
*/
void Buzzer_SetTempThreshold(uint8_t threshold)
{
	g_Buzzer_TempThreshold = threshold;//更新温度阈值
}

/**
*@brief  设置湿度阈值
*@param  threshold 新的湿度阈值
*@retval 无
*/
void Buzzer_SetHumiThreshold(uint8_t threshold)
{
	g_Buzzer_HumiThreshold = threshold;//更新湿度阈值
}

/**
*@brief  设置光照阈值
*@param  threshold 新的光照阈值
*@retval 无
*/
void Buzzer_SetLightThreshold(uint16_t threshold)
{
	g_Buzzer_LightThreshold = threshold;//更新光照阈值
}

/**
*@brief  设置水位阈值
*@param  threshold 新的水位阈值
*@retval 无
*/
void Buzzer_SetWaterThreshold(uint16_t threshold)
{
	g_Buzzer_WaterThreshold = threshold;//更新水位阈值
}

/**
*@brief  获取温度阈值
*@param  无
*@retval 当前温度阈值
*/
uint8_t Buzzer_GetTempThreshold(void)
{
	return g_Buzzer_TempThreshold;//返回当前温度阈值
}

/**
*@brief  获取湿度阈值
*@param  无
*@retval 当前湿度阈值
*/
uint8_t Buzzer_GetHumiThreshold(void)
{
	return g_Buzzer_HumiThreshold;//返回当前湿度阈值
}

/**
*@brief  获取光照阈值
*@param  无
*@retval 当前光照阈值
*/
uint16_t Buzzer_GetLightThreshold(void)
{
	return g_Buzzer_LightThreshold;//返回当前光照阈值
}

/**
*@brief  获取水位阈值
*@param  无
*@retval 当前水位阈值
*/
uint16_t Buzzer_GetWaterThreshold(void)
{
	return g_Buzzer_WaterThreshold;//返回当前水位阈值
}
