#include "uart.h"

int main(void)
{
    // 1. 你的初始化（只保留最简单的串口初始化）
    uart_init(); 

		uint8_t temp=0xff;
		Serial_SendByte(temp);
	
    while(1)
    {
        
        
    }
}