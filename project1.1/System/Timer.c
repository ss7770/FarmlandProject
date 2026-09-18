#include "Timer.h"

//定时器计数变量
static volatile uint32_t Timer_1msCount = 0;//1ms计数器
static volatile uint32_t Timer_10msCount = 0;//10ms计数器
static volatile uint32_t Timer_100msCount = 0;//100ms计数器
static volatile uint32_t Timer_1sCount = 0;//1秒计数器

//定时器回调函数指针数组
static Timer_Callback_t Timer_1msCallbacks[TIMER_MAX_CALLBACKS];//1ms回调函数指针数组
static Timer_Callback_t Timer_10msCallbacks[TIMER_MAX_CALLBACKS];//10ms回调函数指针数组
static Timer_Callback_t Timer_100msCallbacks[TIMER_MAX_CALLBACKS];//100ms回调函数指针数组
static Timer_Callback_t Timer_1sCallbacks[TIMER_MAX_CALLBACKS];//1秒回调函数指针数组

//定时器回调函数计数
static uint8_t Timer_1msCallbackCount = 0;//1ms回调函数个数
static uint8_t Timer_10msCallbackCount = 0;//10ms回调函数个数
static uint8_t Timer_100msCallbackCount = 0;//100ms回调函数个数
static uint8_t Timer_1sCallbackCount = 0;//1秒回调函数个数

/**
  * @brief  定时器2初始化，产生1ms中断
  * @param  无
  * @retval 无
  */
void Timer_Init(void)
{
    TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;//定时器时基结构体
    NVIC_InitTypeDef NVIC_InitStructure;//中断控制器初始化结构体
    
    //使能定时器2时钟
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, ENABLE);//使能TIM2时钟
    
    //定时器2配置
    //系统时钟72MHz，分频72，计数1000，得到1ms中断
    //定时时间 = (72M / 72) / 1000 = 1ms
    TIM_TimeBaseStructure.TIM_Period = 1000 - 1;//自动重装载寄存器值
    TIM_TimeBaseStructure.TIM_Prescaler = 72 - 1;//时钟预分频数
    TIM_TimeBaseStructure.TIM_ClockDivision = TIM_CKD_DIV1;//时钟分频因子
    TIM_TimeBaseStructure.TIM_CounterMode = TIM_CounterMode_Up;//向上计数模式
    TIM_TimeBaseInit(TIM2, &TIM_TimeBaseStructure);//初始化TIM2
    
    //清除更新中断标志
    TIM_ClearFlag(TIM2, TIM_FLAG_Update);//清除TIM2更新中断标志
    
    //使能更新中断
    TIM_ITConfig(TIM2, TIM_IT_Update, ENABLE);//使能TIM2更新中断
    
    //配置NVIC
    NVIC_InitStructure.NVIC_IRQChannel = TIM2_IRQn;//设置中断通道
    NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority = 1;//设置抢占优先级
    NVIC_InitStructure.NVIC_IRQChannelSubPriority = 1;//设置响应优先级
    NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;//使能中断通道
    NVIC_Init(&NVIC_InitStructure);//初始化NVIC
    
    //启动定时器
    TIM_Cmd(TIM2, ENABLE);//使能TIM2
    
    //初始化回调函数指针数组
    uint8_t i;//循环变量
    for(i = 0; i < TIMER_MAX_CALLBACKS; i++)//遍历所有回调函数数组
    {
        Timer_1msCallbacks[i] = 0;//清空1ms回调函数指针
        Timer_10msCallbacks[i] = 0;//清空10ms回调函数指针
        Timer_100msCallbacks[i] = 0;//清空100ms回调函数指针
        Timer_1sCallbacks[i] = 0;//清空1秒回调函数指针
    }
}

/**
  * @brief  定时器2中断服务函数
  * @param  无
  * @retval 无
  */
void TIM2_IRQHandler(void)
{
    uint8_t i;//循环变量
    
    if(TIM_GetITStatus(TIM2, TIM_IT_Update) != RESET)//检查更新中断是否发生
    {
        //清除中断标志
        TIM_ClearITPendingBit(TIM2, TIM_IT_Update);//清除TIM2更新中断挂起位
        
        //1ms计数器增加
        Timer_1msCount++;//1ms计数器自增
        Timer_10msCount++;//10ms计数器自增
        Timer_100msCount++;//100ms计数器自增
        Timer_1sCount++;//1秒计数器自增
        
        //执行1ms回调函数
        for(i = 0; i < Timer_1msCallbackCount; i++)//遍历所有1ms回调函数
        {
            if(Timer_1msCallbacks[i] != 0)//检查回调函数指针是否有效
            {
                Timer_1msCallbacks[i]();//执行回调函数
            }
        }
        
        //10ms处理
        if(Timer_10msCount >= 10)//判断是否到达10ms
        {
            Timer_10msCount = 0;//清零10ms计数器
            for(i = 0; i < Timer_10msCallbackCount; i++)//遍历所有10ms回调函数
            {
                if(Timer_10msCallbacks[i] != 0)//检查回调函数指针是否有效
                {
                    Timer_10msCallbacks[i]();//执行回调函数
                }
            }
        }
        
        //100ms处理
        if(Timer_100msCount >= 100)//判断是否到达100ms
        {
            Timer_100msCount = 0;//清零100ms计数器
            for(i = 0; i < Timer_100msCallbackCount; i++)//遍历所有100ms回调函数
            {
                if(Timer_100msCallbacks[i] != 0)//检查回调函数指针是否有效
                {
                    Timer_100msCallbacks[i]();//执行回调函数
                }
            }
        }
        
        //1秒处理
        if(Timer_1sCount >= 1000)//判断是否到达1秒
        {
            Timer_1sCount = 0;//清零1秒计数器
            for(i = 0; i < Timer_1sCallbackCount; i++)//遍历所有1秒回调函数
            {
                if(Timer_1sCallbacks[i] != 0)//检查回调函数指针是否有效
                {
                    Timer_1sCallbacks[i]();//执行回调函数
                }
            }
        }
    }
}

/**
  * @brief  获取1ms计数器值
  * @param  无
  * @retval 1ms计数器值
  */
uint32_t Timer_Get1msCount(void)
{
    return Timer_1msCount;//返回1ms计数器值
}

/**
  * @brief  获取系统运行时间（ms）
  * @param  无
  * @retval 系统运行毫秒数
  */
uint32_t Timer_GetTick(void)
{
    return Timer_1msCount;//返回系统运行毫秒数
}

/**
  * @brief  延时函数（ms）
  * @param  ms 延时毫秒数
  * @retval 无
  */
void Timer_DelayMs(uint32_t ms)
{
    uint32_t start = Timer_GetTick();//获取起始时间
    while((Timer_GetTick() - start) < ms);//等待直到达到指定延时
}

/**
  * @brief  注册1ms回调函数
  * @param  Callback 回调函数指针
  * @retval 成功返回1，失败返回0
  */
uint8_t Timer_Register1msCallback(Timer_Callback_t Callback)
{
    if(Timer_1msCallbackCount < TIMER_MAX_CALLBACKS && Callback != 0)//检查是否有空位且回调函数有效
    {
        Timer_1msCallbacks[Timer_1msCallbackCount++] = Callback;//存入回调函数指针，计数自增
        return 1;//返回成功
    }
    return 0;//返回失败
}

/**
  * @brief  注册10ms回调函数
  * @param  Callback 回调函数指针
  * @retval 成功返回1，失败返回0
  */
uint8_t Timer_Register10msCallback(Timer_Callback_t Callback)
{
    if(Timer_10msCallbackCount < TIMER_MAX_CALLBACKS && Callback != 0)//检查是否有空位且回调函数有效
    {
        Timer_10msCallbacks[Timer_10msCallbackCount++] = Callback;//存入回调函数指针，计数自增
        return 1;//返回成功
    }
    return 0;//返回失败
}

/**
  * @brief  注册100ms回调函数
  * @param  Callback 回调函数指针
  * @retval 成功返回1，失败返回0
  */
uint8_t Timer_Register100msCallback(Timer_Callback_t Callback)
{
    if(Timer_100msCallbackCount < TIMER_MAX_CALLBACKS && Callback != 0)//检查是否有空位且回调函数有效
    {
        Timer_100msCallbacks[Timer_100msCallbackCount++] = Callback;//存入回调函数指针，计数自增
        return 1;//返回成功
    }
    return 0;//返回失败
}

/**
  * @brief  注册1秒回调函数
  * @param  Callback 回调函数指针
  * @retval 成功返回1，失败返回0
  */
uint8_t Timer_Register1sCallback(Timer_Callback_t Callback)
{
    if(Timer_1sCallbackCount < TIMER_MAX_CALLBACKS && Callback != 0)//检查是否有空位且回调函数有效
    {
        Timer_1sCallbacks[Timer_1sCallbackCount++] = Callback;//存入回调函数指针，计数自增
        return 1;//返回成功
    }
    return 0;//返回失败
}
