#include "stm32f10x.h"                  // Device header
int main(void)
{
    LED_Init();

    while (1)
    {
        LED_SET(1, 1);  // Turn on LED 1
        LED_SET(2, 0);  // Turn off LED 2
        LED_SET(3, 0);  // Turn off LED 3
        LED_SET(4, 0);  // Turn off LED 4
        delay_s(1);  // Delay for 1 second

			  LED_SET(3, 1);  // Turn on LED 3
        LED_SET(1, 0);  // Turn off LED 1
        LED_SET(2, 0);  // Turn off LED 2
        LED_SET(4, 0);  // Turn off LED 4
        delay_s(1);  // Delay for 1 second
			
        LED_SET(2, 1);  // Turn on LED 2
        LED_SET(1, 0);  // Turn off LED 1
        LED_SET(3, 0);  // Turn off LED 3
        LED_SET(4, 0);  // Turn off LED 4
        delay_s(1);  // Delay for 1 second

        LED_SET(4, 1);  // Turn on LED 4
        LED_SET(1, 0);  // Turn off LED 1
        LED_SET(2, 0);  // Turn off LED 2
        LED_SET(3, 0);  // Turn off LED 3
        delay_s(1);  // Delay for 1 second
    }
}