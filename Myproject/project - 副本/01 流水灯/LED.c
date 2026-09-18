#include "stm32f10x.h"                  // Device header
#include "LED.h"
void LED_Init(void)
{
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);

    GPIO_InitTypeDef GPIO_InitStructure;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_0|GPIO_Pin_1;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStructure);
    GPIO_Init(GPIOB, &GPIO_InitStructure);

}

void LED_SET(uint8_t LED_CHOOSE, uint8_t LED_ON)
{
	switch (LED_CHOOSE)
    {
        case 1:
            switch (LED_ON)
            {
                case 1:
                    GPIO_SetBits(GPIOA, GPIO_Pin_0);
                    break;
                case 0:
                    GPIO_ResetBits(GPIOA, GPIO_Pin_0);
                    break;
                default:
                    break;
            }
						break;
        case 2:
            switch (LED_ON)
            {
                case 1:
                    GPIO_SetBits(GPIOB, GPIO_Pin_0);
                    break;
                case 0:
                    GPIO_ResetBits(GPIOB, GPIO_Pin_0);
                    break;
                default:
                    break;
            }
						break;
        case 3:
            switch (LED_ON)
            {
                case 1:
                    GPIO_SetBits(GPIOB, GPIO_Pin_1);
                    break;
                case 0:
                    GPIO_ResetBits(GPIOB, GPIO_Pin_1);
                    break;
                default:
                    break;
            }
						break;
        case 4:
            switch (LED_ON)
            {
                case 1:
                    GPIO_SetBits(GPIOA, GPIO_Pin_1);
                    break;
                case 0:
                    GPIO_ResetBits(GPIOA, GPIO_Pin_1);
                    break;
                default:
                    break;
            }
        default:
            break;
    }
}

void delay_s(int s)
{
    int i, j;
    for (i = 0; i < s; i++)
    {
        for (j = 0; j < 5000000; j++)
        {
        
        }
    }
}