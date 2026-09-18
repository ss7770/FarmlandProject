#ifndef __ESP01S_H
#define __ESP01S_H
#include "stm32f10x.h"
#include <stdbool.h>

//要连接的WiFi热点信息
#define ESP01S_WIFI_SSID       "KA"//WiFi名称
#define ESP01S_WIFI_PASSWORD   "KAITOOOO"//WiFi密码

//ESP-01S服务器端口号
#define ESP01S_SERVER_PORT     "8288"//服务器端口

//ESP-01S工作模式枚举
typedef enum
{
    ESP01S_MODE_STA = 1,//Station模式
    ESP01S_MODE_AP = 2,//AP模式
    ESP01S_MODE_STA_AP = 3//Station+AP混合模式
}ESP01S_Mode_t;

//连接ID枚举（多连接模式使用）
typedef enum
{
    ESP01S_ID_0 = 0,//连接ID 0
    ESP01S_ID_1 = 1,//连接ID 1
    ESP01S_ID_2 = 2,//连接ID 2
    ESP01S_ID_3 = 3,//连接ID 3
    ESP01S_ID_4 = 4,//连接ID 4
    ESP01S_ID_SINGLE = 5//单连接模式
}ESP01S_ID_t;

void ESP01S_Init(void);//ESP-01S初始化
bool ESP01S_AT_Test(void);//AT指令测试
bool ESP01S_Restart(void);//重启模块
bool ESP01S_SetMode(ESP01S_Mode_t mode);//设置工作模式
bool ESP01S_SetSingleConnection(void);//设置单连接模式
bool ESP01S_SetMultiConnection(void);//设置多连接模式
bool ESP01S_ConnectAP(void);//连接WiFi
bool ESP01S_GetIP(char *ipBuf, uint8_t bufSize);//获取IP地址
bool ESP01S_StartServer(char *port);//启动TCP服务器
bool ESP01S_SendData(ESP01S_ID_t id, char *data, uint16_t len);//发送数据
uint16_t ESP01S_ReceiveData(ESP01S_ID_t id, uint8_t *buffer, uint16_t bufferSize);//接收数据
void ESP01S_ClearRxBuffer(void);//清空接收缓冲区
uint8_t ESP01S_GetConnectionStatus(void);//获取连接状态
#endif
