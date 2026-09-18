/**
  ******************************************************************************
  * @file    Project/STM32F10x_StdPeriph_Template/stm32f10x_conf.h 
  * @author  米德半导体应用团队
  * @version V3.5.0
  * @日期    2011-04-08
  * @简介    STM32F10x标准外设库配置文件。
  ******************************************************************************
  * @注意
  *
  * 本固件仅供参考，旨在为客户提供与其产品相关的编码信息，以帮助他们节省时间。
  * 因此，意法半导体对于因使用本固件内容或客户使用其中包含的编码信息
  * 而导致的任何直接、间接或后果性损害，概不负责。
  *
  * <h2><center>&copy; 版权所有 2011 意法半导体</center></h2>
  ******************************************************************************
  */

/* 定义以防止递归包含 --------------------------------------------*/
#ifndef __STM32F10x_CONF_H
#define __STM32F10x_CONF_H

/* 包含文件 ------------------------------------------------------------------*/
/* 取消/注释下面的行以启用/禁用外设头文件的包含 */
#include "stm32f10x_adc.h"
#include "stm32f10x_bkp.h"
#include "stm32f10x_can.h"
#include "stm32f10x_cec.h"
#include "stm32f10x_crc.h"
#include "stm32f10x_dac.h"
#include "stm32f10x_dbgmcu.h"
#include "stm32f10x_dma.h"
#include "stm32f10x_exti.h"
#include "stm32f10x_flash.h"
#include "stm32f10x_fsmc.h"
#include "stm32f10x_gpio.h"
#include "stm32f10x_i2c.h"
#include "stm32f10x_iwdg.h"
#include "stm32f10x_pwr.h"
#include "stm32f10x_rcc.h"
#include "stm32f10x_rtc.h"
#include "stm32f10x_sdio.h"
#include "stm32f10x_spi.h"
#include "stm32f10x_tim.h"
#include "stm32f10x_usart.h"
#include "stm32f10x_wwdg.h"
#include "misc.h" /* 用于NVIC和SysTick的高级函数（CMSIS函数的补充） */

/* 导出的类型 ------------------------------------------------------------*/
/* 导出的常量 --------------------------------------------------------*/
/* 取消下面行的注释以在标准外设库驱动程序代码中扩展"assert_param"宏 */
/* #define USE_FULL_ASSERT    1 */

/* 导出的宏 ------------------------------------------------------------*/
#ifdef  USE_FULL_ASSERT

/**
  * @brief  assert_param宏用于函数的参数检查。
  * @param  expr: 如果expr为假，它会调用assert_failed函数，该函数报告
  *         调用失败的源文件名和源代码行号。如果expr为真，则不返回任何值。
  * @retval 无
  */
  #define assert_param(expr) ((expr) ? (void)0 : assert_failed((uint8_t *)__FILE__, __LINE__))
/* 导出的函数 ------------------------------------------------------- */
  void assert_failed(uint8_t* file, uint32_t line);
#else
  #define assert_param(expr) ((void)0)
#endif /* USE_FULL_ASSERT */

#endif /* __STM32F10x_CONF_H */

/******************* (C) COPYRIGHT 2011 意法半导体 *****文件结束****/
