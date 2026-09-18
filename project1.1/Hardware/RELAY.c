#include "RELAY.h"

/**
* 函数名：继电器初始化
* 描述：初始化继电器控制引脚PB10为推挽输出模式，默认断开
* 输入：无
* 输出：无
*/
void Relay_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;
    
    //使能GPIOB端口时钟
    RCC_APB2PeriphClockCmd(RELAY_CLOCK, ENABLE);
    
    //配置GPIO结构体参数
    GPIO_InitStructure.GPIO_Pin = RELAY_PIN;//选择PB10引脚
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;//输出速度50MHz
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;//推挽输出模式
    GPIO_Init(RELAY_PORT, &GPIO_InitStructure);//初始化GPIO
    
    //默认断开继电器
    RELAY_OFF;
}

/**
* 函数名：继电器控制
* 描述：控制继电器吸合或断开
* 输入：flag-0：断开继电器，1：吸合继电器
* 输出：无
*/
void Relay_Control(uint8_t flag)
{
    if(flag == 1)
    {
        RELAY_ON;//吸合继电器
    }
    else
    {
        RELAY_OFF;//断开继电器
    }
}
