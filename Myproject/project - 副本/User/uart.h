#ifndef _UART_H_
#define _UART_H_

#include "stm32f10x.h"
#include <string.h>
void USART1_IRQHandler(void);
void Serial_SendByte(uint8_t Byte);
void Serial_SendString(char *String);
void Serial_SendArray(uint8_t *Array, uint16_t Length);

#endif
