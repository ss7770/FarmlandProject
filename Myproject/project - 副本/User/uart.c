#include "uart.h"
#include <string.h>   // 别忘了加这个，用来 memset 和 memcpy

// 全局变量（和原来一样）
uint8_t Serial_RxBuffer[100];
uint8_t Serial_TxBuffer[100];
char Serial_RxPacket[100];
uint8_t Serial_RxFlag = 0;
uint16_t Serial_RxLength = 0;

void uart_init(void)
{
    // ---------- 第1处修改：时钟使能 ----------
    // USART1 在 APB2 总线上，原来你写成了 USART2 或 APB1，现在改成下面这个
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1, ENABLE);   // 改这里！
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
    RCC_AHBPeriphClockCmd(RCC_AHBPeriph_DMA1, ENABLE);

    // ---------- GPIO 配置（完全没动，和你原来一模一样） ----------
    GPIO_InitTypeDef GPIO_InitStructure;
    // TX - PA9
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_9;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStructure);
    // RX - PA10
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_10;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IPU;   // 上拉输入
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStructure);

    // ---------- USART 配置（原来你用了 USART2，现在都改成 USART1） ----------
    USART_InitTypeDef USART_InitStructure;
    USART_InitStructure.USART_BaudRate = 115200;
    USART_InitStructure.USART_WordLength = USART_WordLength_8b;
    USART_InitStructure.USART_StopBits = USART_StopBits_1;
    USART_InitStructure.USART_Parity = USART_Parity_No;
    USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
    USART_Init(USART1, &USART_InitStructure);          // 改这里！原来是 USART2

    // ---------- 第2处修改：使能 IDLE 中断（原来你只使能了 RXNE，没使能 IDLE） ----------
    USART_ITConfig(USART1, USART_IT_IDLE, ENABLE);     // 一定要加这一行！

    // ---------- NVIC 中断配置 ----------
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);
    NVIC_InitTypeDef NVIC_InitStructure;
    NVIC_InitStructure.NVIC_IRQChannel = USART1_IRQn;  // 改这里！原来是 USART2_IRQn
    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 1;
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 1;
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&NVIC_InitStructure);

    // ---------- DMA 配置 ----------
    DMA_InitTypeDef DMA_InitStructure;
    
    // Rx - DMA1 通道5
    DMA_DeInit(DMA1_Channel5);
    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&USART1->DR;
    DMA_InitStructure.DMA_MemoryBaseAddr = (uint32_t)Serial_RxBuffer;
    DMA_InitStructure.DMA_DIR = DMA_DIR_PeripheralSRC;
    DMA_InitStructure.DMA_BufferSize = 100;            // 第3处修改：原来是 0，改成 100！
    DMA_InitStructure.DMA_PeripheralInc = DMA_PeripheralInc_Disable;
    DMA_InitStructure.DMA_MemoryInc = DMA_MemoryInc_Enable;
    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
    DMA_InitStructure.DMA_MemoryDataSize = DMA_MemoryDataSize_Byte;
    DMA_InitStructure.DMA_Mode = DMA_Mode_Circular;
    DMA_InitStructure.DMA_Priority = DMA_Priority_Medium;
    DMA_InitStructure.DMA_M2M = DMA_M2M_Disable;
    DMA_Init(DMA1_Channel5, &DMA_InitStructure);
    USART_DMACmd(USART1, USART_DMAReq_Rx, ENABLE);
    DMA_Cmd(DMA1_Channel5, ENABLE);
    DMA_ClearFlag(DMA1_FLAG_TC5 | DMA1_FLAG_HT5 | DMA1_FLAG_TE5);

    // Tx - DMA1 通道4（保留不变）
    DMA_DeInit(DMA1_Channel4);
    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&USART1->DR;
    DMA_InitStructure.DMA_MemoryBaseAddr = (uint32_t)Serial_TxBuffer;
    DMA_InitStructure.DMA_DIR = DMA_DIR_PeripheralDST;
    DMA_InitStructure.DMA_BufferSize = 0;
    DMA_InitStructure.DMA_PeripheralInc = DMA_PeripheralInc_Disable;
    DMA_InitStructure.DMA_MemoryInc = DMA_MemoryInc_Enable;
    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
    DMA_InitStructure.DMA_MemoryDataSize = DMA_MemoryDataSize_Byte;
    DMA_InitStructure.DMA_Mode = DMA_Mode_Normal;
    DMA_InitStructure.DMA_Priority = DMA_Priority_Medium;
    DMA_InitStructure.DMA_M2M = DMA_M2M_Disable;
    DMA_Init(DMA1_Channel4, &DMA_InitStructure);
    USART_DMACmd(USART1, USART_DMAReq_Tx, ENABLE);
    // DMA_Cmd(DMA1_Channel4, ENABLE);  // 发送时才开，先注释掉

    // ---------- 最后，打开串口 ----------
    USART_Cmd(USART1, ENABLE);
}

// 串口中断函数（函数名必须叫这个）
void USART1_IRQHandler(void)
{
    if (USART_GetITStatus(USART1, USART_IT_IDLE) == SET)
    {
        volatile uint32_t tmp;
        tmp = USART1->SR;
        tmp = USART1->DR;

        DMA_Cmd(DMA1_Channel5, DISABLE);

        uint16_t data_len = 100 - DMA1_Channel5->CNDTR;
        if (data_len > 0)
        {
            memset(Serial_RxPacket, 0, 100);
            memcpy(Serial_RxPacket, Serial_RxBuffer, data_len);
            Serial_RxLength = data_len;
            Serial_RxFlag = 1;
        }

        DMA1_Channel5->CNDTR = 100;
        DMA_Cmd(DMA1_Channel5, ENABLE);

        USART_ClearITPendingBit(USART1, USART_IT_IDLE);
    }
}

#include <string.h>   // 用到 strlen 和 memcpy，需要包含

// 发送一个字节
// 参数：Byte - 要发送的单个字节数据
void Serial_SendByte(uint8_t Byte)
{
    // 1. 把数据写到 DMA 发送缓冲区的第一个位置
    Serial_TxBuffer[0] = Byte;

    // 2. 关闭 DMA 通道4（防止正在传输时修改配置）
    DMA_Cmd(DMA1_Channel4, DISABLE);

    // 3. 设置要发送的字节数：1 个字节
    DMA1_Channel4->CNDTR = 1;      // 注意：寄存器名是 CNDTR，不是 CNTTR！

    // 4. 设置内存地址（指向缓冲区首地址）
    DMA1_Channel4->CMAR = (uint32_t)Serial_TxBuffer;  // 或者 &Serial_TxBuffer[0]

    // 5. 重新开启 DMA 通道，开始发送
    DMA_Cmd(DMA1_Channel4, ENABLE);
}

// 全局变量（已在 uart.c 中定义）
extern uint8_t Serial_TxBuffer[100];   // DMA 发送缓冲区

// ---------- 函数1：发送任意字节数组 ----------
// 参数：Array - 要发送的数据数组（数组名），Length - 要发送的字节数
void Serial_SendArray(uint8_t *Array, uint16_t Length)
{
    // 边界保护：长度不能超过缓冲区大小
    if (Length == 0 || Length > 100) return;

    // 1. 把要发送的数据拷贝到 DMA 发送缓冲区
    for (uint16_t i = 0; i < Length; i++)
    {
        Serial_TxBuffer[i] = Array[i];
    }

    // 2. 关闭 DMA 通道4，防止正在传输
    DMA_Cmd(DMA1_Channel4, DISABLE);

    // 3. 设置要发送的字节数（注意：寄存器名是 CNDTR，不是 CNTDR）
    DMA1_Channel4->CNDTR = Length;

    // 4. 设置源地址（其实可以不重复设置，如果没变过，但为了保险）
    DMA1_Channel4->CMAR = (uint32_t)Serial_TxBuffer;

    // 5. 重新开启 DMA 通道，开始发送
    DMA_Cmd(DMA1_Channel4, ENABLE);
}

// ---------- 函数2：发送字符串（自动计算长度） ----------
// 参数：String - 以 '\0' 结尾的字符串指针
void Serial_SendString(char *String)
{
    uint16_t length = strlen(String);          // 计算字符串长度（不含'\0'）
    if (length == 0 || length > 100) return;   // 边界保护

    // 把字符串拷贝到 DMA 缓冲区（包含'\0'？不需要，串口发送一般不含结束符）
    memcpy(Serial_TxBuffer, String, length);

    // 同样操作：关DMA -> 设长度 -> 开DMA
    DMA_Cmd(DMA1_Channel4, DISABLE);
    DMA1_Channel4->CNDTR = length;
    DMA1_Channel4->CMAR = (uint32_t)Serial_TxBuffer;
    DMA_Cmd(DMA1_Channel4, ENABLE);
}