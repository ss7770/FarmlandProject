#include "stm32f10x.h"                  // Device header
#include "LED.h"
void LED_Init(void)
{
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);

    GPIO_InitTypeDef GPIO_InitStructure;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_Out_PP;
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_0|GPIO_Pin_1|GPIO_Pin_2|GPIO_Pin_3|GPIO_Pin_4|GPIO_Pin_5|GPIO_Pin_6|GPIO_Pin_7;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStructure);
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_0|GPIO_Pin_1;
    GPIO_Init(GPIOB, &GPIO_InitStructure);
    

}
int i=0;
int j=0;
void LED_Display(int j, int i)
{
    // 1. 【关键！】不管接下来要显示哪边，先把两个公共端全部关掉
		GPIO_ResetBits(GPIOB, GPIO_Pin_0 | GPIO_Pin_1);

    // 2. 根据 j 的值，选择打开哪一边的数码管
    switch(j)
    {
        case 1: // 打开左边
            GPIO_SetBits(GPIOB, GPIO_Pin_0); 
            break;
        case 2: // 打开右边
            GPIO_SetBits(GPIOB, GPIO_Pin_1);
            break;
        default:
            break;
    }
    // 3. 设置想要显示的段码（PA 口），根据 i 的值决定显示的数字
    // 注意：因为 PA0~PA7 是共用线，所以不管 j 选左还是选右，这里都会生效
    switch(i)
    {
        case 6: 
            GPIO_ResetBits(GPIOA, GPIO_Pin_0|GPIO_Pin_2|GPIO_Pin_3|GPIO_Pin_4|GPIO_Pin_5|GPIO_Pin_6);
            GPIO_SetBits(GPIOA, GPIO_Pin_1|GPIO_Pin_7); 
            break;
        case 7:
            GPIO_ResetBits(GPIOA, GPIO_Pin_0|GPIO_Pin_1|GPIO_Pin_2);
            GPIO_SetBits(GPIOA, GPIO_Pin_6|GPIO_Pin_7|GPIO_Pin_3|GPIO_Pin_4|GPIO_Pin_5);
            break;
        case 8:
            GPIO_ResetBits(GPIOA, GPIO_Pin_0|GPIO_Pin_1|GPIO_Pin_2|GPIO_Pin_6|GPIO_Pin_7|GPIO_Pin_3|GPIO_Pin_4|GPIO_Pin_5);
            break;
        default:
            break;
    }
    
}

void relay(uint32_t us)
{
    uint32_t i;
    for(i=0; i<us*8; i++); 
}
