#ifndef __SERIAL_H
#define __SERIAL_H
#include "stm32f10x.h"
#include <stdio.h>
#include <stdarg.h>
#include <stdbool.h>

//串口1用于ESP-01S通信（AT指令和数据传输）-使用USART2（PA2-TX，PA3-RX）
#define ESP01S_USARTx				USART2//使用USART2
#define ESP01S_USART_CLK			RCC_APB1Periph_USART2//USART2时钟
#define ESP01S_USART_APBxClkCmd		RCC_APB1PeriphClockCmd//时钟使能函数
#define ESP01S_USART_BAUDRATE		115200//波特率

//串口1GPIO时钟（USART2使用GPIOA）
#define ESP01S_USART_GPIO_CLK			RCC_APB2Periph_GPIOA//GPIOA时钟
#define ESP01S_USART_GPIO_APBxClkCmd	RCC_APB2PeriphClockCmd//时钟使能函数

//串口1引脚定义（USART2的TX和RX分别对应PA2和PA3）
#define ESP01S_USART_TX_PORT	GPIOA//TX引脚端口（PA2）
#define ESP01S_USART_TX_PIN		GPIO_Pin_2//TX引脚
#define ESP01S_USART_RX_PORT	GPIOA//RX引脚端口（PA3）
#define ESP01S_USART_RX_PIN		GPIO_Pin_3//RX引脚

//串口1中断配置
#define ESP01S_USART_IRQ			USART2_IRQn//中断通道
#define ESP01S_USART_IRQHandler		USART2_IRQHandler//中断处理函数

//串口2（调试串口）配置，用于输出调试信息 - 使用USART3（PB10-TX，PB11-RX）
#define DEBUG_USARTx					USART3//使用USART3
#define DEBUG_USART_CLK					RCC_APB1Periph_USART3//USART3时钟
#define DEBUG_USART_APBxClkCmd			RCC_APB1PeriphClockCmd//时钟使能函数
#define DEBUG_USART_BAUDRATE			115200//波特率

//串口2 GPIO引脚宏定义（PB10-TX，PB11-RX）
#define DEBUG_USART_GPIO_CLK			(RCC_APB2Periph_GPIOB)//GPIOB时钟
#define DEBUG_USART_GPIO_APBxClkCmd		RCC_APB2PeriphClockCmd//时钟使能函数
#define DEBUG_USART_TX_GPIO_PORT		GPIOB//TX引脚端口（PB10）
#define DEBUG_USART_TX_GPIO_PIN			GPIO_Pin_10//TX引脚
#define DEBUG_USART_RX_GPIO_PORT		GPIOB//RX引脚端口（PB11）
#define DEBUG_USART_RX_GPIO_PIN			GPIO_Pin_11//RX引脚

//串口2中断配置
#define DEBUG_USART_IRQ					USART3_IRQn//中断通道
#define DEBUG_USART_IRQHandler			USART3_IRQHandler//中断处理函数

//接收缓冲区大小（字节）
#define SERIAL_RX_BUF_SIZE		1024

//串口接收帧结构体
typedef struct
{
    uint8_t RxBuf[SERIAL_RX_BUF_SIZE];//接收缓冲区（存储接收到的数据）
    volatile uint16_t RxCount;//已接收数据长度（缓冲区有效数据长度）
    volatile uint8_t RxCompleteFlag;//帧接收完成标志（1表示已接收到完整一帧）
    volatile uint8_t RxNewLineFlag;//接收到换行标志（1表示收到\n）
    volatile uint8_t TcpClosedFlag;//TCP连接关闭标志（1表示连接已断开）
}Serial_RxFrame_t;

//串口1接收帧（全局变量，供其他模块访问）
extern Serial_RxFrame_t g_Serial1_RxFrame;

void Serial1_Init(void);//初始化串口1（ESP-01S通信）
void Serial1_SendByte(uint8_t ch);//发送一个字节
void Serial1_SendArray(uint8_t *array, uint16_t num);//发送数组
void Serial1_SendString(char *str);//发送字符串（自动识别结束符\0）
void Serial1_SendHalfWord(uint16_t ch);//发送16位数据（高8位+低8位）
void Serial1_Printf(char *format, ...);//格式化打印
void Serial1_ClearRxBuffer(void);//清空接收缓冲区

//串口2调试输出函数（用于输出调试信息）
void Serial2_Init(void);//初始化串口2（调试串口）
void Serial2_SendByte(uint8_t ch);//发送一个字节
void Serial2_SendString(char *str);//发送字符串
void Serial2_Printf(char *format, ...);//格式化打印（类似printf）
#endif
