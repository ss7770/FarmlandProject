#include "stm32f10x.h"                  // Device header
#include "LED.h"
int main(void)
{
    LED_Init();

    while (1)
    {
        LED_Display(1,8);
        relay(2000);
        LED_Display(2,7);
        relay(2000);
    }
}