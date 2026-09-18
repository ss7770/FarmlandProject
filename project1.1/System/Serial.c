#include "Serial.h"
#include "Delay.h"

//串口1接收帧（全局变量定义）
Serial_RxFrame_t g_Serial1_RxFrame = {0};

//静态函数声明
static void Serial1_NVIC_Config(void);//串口1中断优先级配置
static void Serial2_NVIC_Config(void);//串口2中断优先级配置

/**
  *@brief  串口1初始化（ESP-01S通信，使用USART2，PA2-TX，PA3-RX）
  *@param  无
  *@retval 无
  */
void Serial1_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;//GPIO初始化结构体
    USART_InitTypeDef USART_InitStructure;//USART初始化结构体

    //使能GPIO时钟（PA2和PA3的时钟）
    ESP01S_USART_GPIO_APBxClkCmd(ESP01S_USART_GPIO_CLK, ENABLE);

    //使能USART时钟（USART2的时钟）
    ESP01S_USART_APBxClkCmd(ESP01S_USART_CLK, ENABLE);

    //配置TX（PA2）为复用推挽输出
    GPIO_InitStructure.GPIO_Pin = ESP01S_USART_TX_PIN;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;//复用推挽输出
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;//速度50MHz
    GPIO_Init(ESP01S_USART_TX_PORT, &GPIO_InitStructure);

    //配置RX（PA3）为上拉输入
    GPIO_InitStructure.GPIO_Pin = ESP01S_USART_RX_PIN;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;//上拉输入
    GPIO_Init(ESP01S_USART_RX_PORT, &GPIO_InitStructure);

    //配置USART参数
    USART_InitStructure.USART_BaudRate = ESP01S_USART_BAUDRATE;//波特率115200
    USART_InitStructure.USART_WordLength = USART_WordLength_8b;//数据位8位
    USART_InitStructure.USART_StopBits = USART_StopBits_1;//停止位1位
    USART_InitStructure.USART_Parity = USART_Parity_No;//无奇偶校验
    USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//无硬件流控制
    USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;//使能接收和发送
    USART_Init(ESP01S_USARTx, &USART_InitStructure);//初始化USART

    //配置中断优先级
    Serial1_NVIC_Config();

    //使能接收中断（当接收到数据时触发中断）
    USART_ITConfig(ESP01S_USARTx, USART_IT_RXNE, ENABLE);

    //使能空闲中断（用于检测数据帧结束）
    USART_ITConfig(ESP01S_USARTx, USART_IT_IDLE, ENABLE);

    //使能串口
    USART_Cmd(ESP01S_USARTx, ENABLE);

    //清空接收缓冲区（初始状态）
    Serial1_ClearRxBuffer();

    //初始化TCP关闭标志
    g_Serial1_RxFrame.TcpClosedFlag = 0;
}

/**
  *@brief  串口1中断优先级配置（USART2中断）
  *@param  无
  *@retval 无
  */
static void Serial1_NVIC_Config(void)
{
    NVIC_InitTypeDef NVIC_InitStructure;//NVIC初始化结构体

    //设置中断优先级分组为组2（2位抢占优先级，2位响应优先级）
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);

    //配置串口1中断
    NVIC_InitStructure.NVIC_IRQChannel = ESP01S_USART_IRQ;//中断通道：USART2
    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 1;//抢占优先级：1
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 1;//响应优先级：1
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;//使能中断通道
    NVIC_Init(&NVIC_InitStructure);
}

/**
  *@brief  串口2中断优先级配置（USART3中断）
  *@param  无
  *@retval 无
  */
static void Serial2_NVIC_Config(void)
{
    NVIC_InitTypeDef NVIC_InitStructure;//NVIC初始化结构体

    //配置串口2中断（用于调试，只使能接收中断）
    NVIC_InitStructure.NVIC_IRQChannel = DEBUG_USART_IRQ;//中断通道：USART3
    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 2;//抢占优先级：2
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 2;//响应优先级：2
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;//使能中断通道
    NVIC_Init(&NVIC_InitStructure);
}

/**
  *@brief  该函数处理USART2中断请求（ESP-01S通信）
  *@param  无
  *@retval 无
  */
void USART2_IRQHandler(void)
{
    uint8_t data;//接收到的数据
    uint8_t temp;//临时变量，用于清除空闲中断标志

    //检查接收中断标志（RXNE：接收数据寄存器非空）
    if(USART_GetITStatus(ESP01S_USARTx, USART_IT_RXNE) != RESET)
    {
        //读取接收到的数据（读取操作会自动清除中断标志）
        data = USART_ReceiveData(ESP01S_USARTx);

        //如果缓冲区未满，存入缓冲区
        if(g_Serial1_RxFrame.RxCount < SERIAL_RX_BUF_SIZE - 1)
        {
            g_Serial1_RxFrame.RxBuf[g_Serial1_RxFrame.RxCount] = data;//存入缓冲区
            g_Serial1_RxFrame.RxCount++;//已接收数据长度加1

            //检测换行符（\n，ASCII码0x0A）
            //AT指令响应通常以\r\n结尾，检测到\n表示一帧结束
            if(data == '\n')
            {
                g_Serial1_RxFrame.RxNewLineFlag = 1;//设置换行标志
                g_Serial1_RxFrame.RxCompleteFlag = 1;//设置接收完成标志
            }
        }
        else//缓冲区满，强制标记接收完成
        {
            g_Serial1_RxFrame.RxCompleteFlag = 1;//设置接收完成标志
        }
    }

    //检查空闲中断标志（IDLE：数据帧接收完毕）
    if(USART_GetITStatus(ESP01S_USARTx, USART_IT_IDLE) != RESET)
    {
        //标记数据帧接收完成
        g_Serial1_RxFrame.RxCompleteFlag = 1;

        //清除空闲中断标志位（先读USART_SR，再读USART_DR）
        temp = USART_ReceiveData(ESP01S_USARTx);
        (void)temp;//防止编译器警告

        //检查TCP连接是否已关闭
        if(strstr((char*)g_Serial1_RxFrame.RxBuf, "CLOSED") != NULL)
        {
            g_Serial1_RxFrame.TcpClosedFlag = 1;
        }
    }
}

/**
  *@brief  串口3中断服务函数（调试串口接收）
  *@param  无
  *@retval 无
  */
void USART3_IRQHandler(void)
{
    uint8_t data;//接收到的数据

    //检查接收中断标志
    if(USART_GetITStatus(DEBUG_USARTx, USART_IT_RXNE) != RESET)
    {
        //读取接收到的数据（清除中断标志）
        data = USART_ReceiveData(DEBUG_USARTx);
        //此处可以添加调试数据处理逻辑，目前仅读取以清除中断
        (void)data;
    }
}

/**
  *@brief  串口1发送一个字节（ESP-01S通信，USART2）
  *@param  ch要发送的字节
  *@retval 无
  */
void Serial1_SendByte(uint8_t ch)
{
    USART_SendData(ESP01S_USARTx, ch);//发送数据
    //等待发送数据寄存器为空（TXE：发送数据寄存器空标志）
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TXE) == RESET);
}

/**
  *@brief  串口1发送数组
  *@param  array数组指针
  *@param  num数组长度
  *@retval 无
  */
void Serial1_SendArray(uint8_t *array, uint16_t num)
{
    uint16_t i;//循环变量

    for(i = 0; i < num; i++)
    {
        Serial1_SendByte(array[i]);//逐个字节发送
    }

    //等待发送完成（TC：发送完成标志）
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TC) == RESET);
}

/**
  *@brief  串口1发送字符串
  *@param  str字符串指针
  *@retval 无
  */
void Serial1_SendString(char *str)
{
    while(*str != '\0')//遍历字符串直到结束符
    {
        Serial1_SendByte(*str);//发送当前字符
        str++;//指针移动到下一个字符
    }

    //等待发送完成（TC：发送完成标志）
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TC) == RESET);
}

/**
  *@brief  串口1发送16位数据
  *@param  ch16位数据
  *@retval 无
  */
void Serial1_SendHalfWord(uint16_t ch)
{
    uint8_t temp_h, temp_l;//临时变量

    //取出高八位
    temp_h = (ch & 0xFF00) >> 8;
    //取出低八位
    temp_l = ch & 0xFF;

    //发送高八位
    USART_SendData(ESP01S_USARTx, temp_h);
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TXE) == RESET);

    //发送低八位
    USART_SendData(ESP01S_USARTx, temp_l);
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TXE) == RESET);

    //等待发送完成
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TC) == RESET);
}

/**
  *@brief  串口1格式化打印
  *@param  format格式化字符串
  *@param  ... 可变参数
  *@retval 无
  *@note   使用方法类似printf，例如：Serial1_Printf("温度:%d℃\r\n", temp);
  */
void Serial1_Printf(char *format, ...)
{
    char buf[256];//缓冲区
    va_list ap;//可变参数列表
    char *p;//指针

    va_start(ap, format);//初始化可变参数列表
    vsprintf(buf, format, ap);//格式化字符串
    va_end(ap);//结束可变参数处理

    p = buf;//指向缓冲区起始位置
    while(*p != '\0')//遍历缓冲区直到结束符
    {
        Serial1_SendByte(*p);//发送当前字符
        p++;//指针移动到下一个字符
    }

    //等待发送完成
    while(USART_GetFlagStatus(ESP01S_USARTx, USART_FLAG_TC) == RESET);
}

/**
  *@brief  清空串口1接收缓冲区
  *@param  无
  *@retval 无
  */
void Serial1_ClearRxBuffer(void)
{
    uint16_t i;//循环变量
    
    //重置计数器
    g_Serial1_RxFrame.RxCount = 0;
    //清除标志位
    g_Serial1_RxFrame.RxCompleteFlag = 0;
    g_Serial1_RxFrame.RxNewLineFlag = 0;
    g_Serial1_RxFrame.TcpClosedFlag = 0;

    //清空缓冲区内容（全部置0）
    for(i = 0; i < SERIAL_RX_BUF_SIZE; i++)
    {
        g_Serial1_RxFrame.RxBuf[i] = 0;
    }
}

/**
  *@brief  串口2初始化（调试串口，使用USART3，PB10-TX，PB11-RX）
  *@param  无
  *@retval 无
  */
void Serial2_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;//GPIO初始化结构体
    USART_InitTypeDef USART_InitStructure;//USART初始化结构体

    //使能GPIOB时钟（PB10和PB11）
    DEBUG_USART_GPIO_APBxClkCmd(DEBUG_USART_GPIO_CLK, ENABLE);

    //使能USART3时钟
    DEBUG_USART_APBxClkCmd(DEBUG_USART_CLK, ENABLE);

    //配置TX（PB10）为复用推挽输出
    GPIO_InitStructure.GPIO_Pin = DEBUG_USART_TX_GPIO_PIN;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;//复用推挽输出
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;//速度50MHz
    GPIO_Init(DEBUG_USART_TX_GPIO_PORT, &GPIO_InitStructure);

    //配置RX（PB11）为浮空输入
    GPIO_InitStructure.GPIO_Pin = DEBUG_USART_RX_GPIO_PIN;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;//浮空输入
    GPIO_Init(DEBUG_USART_RX_GPIO_PORT, &GPIO_InitStructure);

    //配置USART参数
    USART_InitStructure.USART_BaudRate = DEBUG_USART_BAUDRATE;//波特率115200
    USART_InitStructure.USART_WordLength = USART_WordLength_8b;//数据位8位
    USART_InitStructure.USART_StopBits = USART_StopBits_1;//停止位1位
    USART_InitStructure.USART_Parity = USART_Parity_No;//无奇偶校验
    USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//无硬件流控制
    USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;//使能接收和发送
    USART_Init(DEBUG_USARTx, &USART_InitStructure);//初始化USART3

    //配置中断优先级（用于接收调试命令）
    Serial2_NVIC_Config();

    //使能接收中断（用于接收上位机调试命令）
    USART_ITConfig(DEBUG_USARTx, USART_IT_RXNE, ENABLE);

    //使能串口
    USART_Cmd(DEBUG_USARTx, ENABLE);

    //输出初始化完成信息
    Serial2_Printf("Debug Serial2 (USART3) Initialized!\r\n");
}

/**
  *@brief  串口2发送一个字节（调试串口，USART3）
  *@param  ch要发送的字节
  *@retval 无
  */
void Serial2_SendByte(uint8_t ch)
{
    USART_SendData(DEBUG_USARTx, ch);//发送数据
    //等待发送数据寄存器为空
    while(USART_GetFlagStatus(DEBUG_USARTx, USART_FLAG_TXE) == RESET);
}

/**
  *@brief  串口2发送字符串
  *@param  str字符串指针
  *@retval 无
  */
void Serial2_SendString(char *str)
{
    while(*str != '\0')//遍历字符串直到结束符
    {
        Serial2_SendByte(*str);//发送当前字符
        str++;//指针移动到下一个字符
    }
}

/**
  *@brief  串口2格式化打印（调试输出）
  *@param  format格式化字符串
  *@param  ... 可变参数
  *@retval 无
  */
void Serial2_Printf(char *format, ...)
{
    char buf[256];//缓冲区
    va_list ap;//可变参数列表
    char *p;//指针

    va_start(ap, format);//初始化可变参数列表
    vsprintf(buf, format, ap);//格式化字符串
    va_end(ap);//结束可变参数处理

    p = buf;//指向缓冲区起始位置
    while(*p != '\0')//遍历缓冲区直到结束符
    {
        Serial2_SendByte(*p);//发送当前字符
        p++;//指针移动到下一个字符
    }
}
