#include "DHT11.h"
#include "Delay.h"

/**
  *@brief  DHT11_IO输出模式配置
  *@param  无
  *@retval 无
  */
static void DHT11_IO_OUT(void)
{
    GPIO_InitTypeDef GPIO_InitStruct;//GPIO初始化结构体

    //配置为推挽输出模式
    GPIO_InitStruct.GPIO_Pin = DHT11_IO;
    GPIO_InitStruct.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_InitStruct.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(DHT11_GPIO, &GPIO_InitStruct);
}

/**
  *@brief  DHT11_IO输入模式配置
  *@param  无
  *@retval 无
  */
static void DHT11_IO_IN(void)
{
    GPIO_InitTypeDef GPIO_InitStruct;//GPIO初始化结构体

    //配置为上拉输入模式
    GPIO_InitStruct.GPIO_Pin = DHT11_IO;
    GPIO_InitStruct.GPIO_Mode = GPIO_Mode_IPU;
    GPIO_Init(DHT11_GPIO, &GPIO_InitStruct);
}

/**
  *@brief  发送起始信号
  *@param  无
  *@retval 无
  */
static void DHT11_Start(void)
{
    //将IO设置为输出模式
    DHT11_IO_OUT();
    //主机拉低至少18ms
    GPIO_ResetBits(DHT11_GPIO, DHT11_IO);
    Delay_ms(20);

    //主机拉高20~40us
    GPIO_SetBits(DHT11_GPIO, DHT11_IO);
    Delay_us(30);
}

/**
  *@brief  等待应答信号
  *@param  无
  *@retval true-应答成功，false-应答失败
  */
static bool DHT11_Ack(void)
{
    uint8_t time_out = 0;//超时计数器

    //将IO设置为输入模式
    DHT11_IO_IN();

    //等待DHT11拉低80us
    while(!GPIO_ReadInputDataBit(DHT11_GPIO, DHT11_IO) && time_out < 100)
    {
        time_out++;
        Delay_us(1);
    }
    if(time_out >= 100)
        return false;//应答失败

    time_out = 0;//重置超时计数器

    //等待DHT11拉高80us
    while(GPIO_ReadInputDataBit(DHT11_GPIO, DHT11_IO) && time_out < 100)
    {
        time_out++;
        Delay_us(1);
    }
    if(time_out >= 100)
        return false;//应答失败

    return true;//应答成功
}

/**
  *@brief  读取1位数据
  *@param  无
  *@retval 读取到的位数据（0或1）
  */
static uint8_t DHT11_ReadBit(void)
{
    uint8_t time_out = 0;//超时计数器

    //等待信号拉高（表示数据开始）
    while(GPIO_ReadInputDataBit(DHT11_GPIO, DHT11_IO) && time_out < 55)
    {
        time_out++;
        Delay_us(1);
    }

    time_out = 0;//重置超时计数器

    //等待信号拉低（数据位传输）
    while(!GPIO_ReadInputDataBit(DHT11_GPIO, DHT11_IO) && time_out < 55)
    {
        time_out++;
        Delay_us(1);
    }

    //延时40us后判断电平状态
    Delay_us(40);

    //高电平表示1，低电平表示0
    if(GPIO_ReadInputDataBit(DHT11_GPIO, DHT11_IO))
        return 1;
    return 0;
}

/**
  *@brief  读取1个字节数据
  *@param  无
  *@retval 读取到的字节数据
  */
static uint8_t DHT11_ReadByte(void)
{
    uint8_t i;//循环变量
    uint8_t data = 0;//数据变量

    for(i = 0; i < 8; i++)
    {
        data <<= 1;
        data |= DHT11_ReadBit();//读取当前位数据
    }
    return data;
}

/**
  *@brief  DHT11初始化
  *@param  无
  *@retval 无
  */
void DHT11_Init(void)
{
    //使能GPIO时钟
    RCC_APB2PeriphClockCmd(DHT11_RCC, ENABLE);

    //发送起始信号并等待应答
    DHT11_Start();
    DHT11_Ack();
}

/**
  *@brief  读取温湿度数据
  *@param  humi 湿度数据指针
  *@param  temp 温度数据指针
  *@retval true-读取成功，false-读取失败
  */
bool DHT11_ReadData(uint8_t *humi, uint8_t *temp)
{
    uint8_t buf[5];//数据缓冲区
    uint8_t i;//循环变量

    //发送起始信号
    DHT11_Start();

    //等待应答信号
    if(DHT11_Ack() == true)
    {
        //读取40位数据
        for(i = 0; i < 5; i++)
        {
            buf[i] = DHT11_ReadByte();
        }

        //校验和验证
        if(buf[0] + buf[1] + buf[2] + buf[3] == buf[4])
        {
            //湿度值
            *humi = buf[0];
            //温度值
            *temp = buf[2];
            return true;//读取成功
        }
    }
    return false;//读取失败
}
