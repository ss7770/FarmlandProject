#include "ESP01S.h"
#include "Serial.h"
#include "Delay.h"
#include "Timer.h"
#include <string.h>
#include <stdio.h>

//接收缓冲区
#define ESP01S_RX_BUF    g_Serial1_RxFrame.RxBuf
#define ESP01S_RX_COUNT  g_Serial1_RxFrame.RxCount
#define ESP01S_RX_FLAG   g_Serial1_RxFrame.RxCompleteFlag

//静态函数声明
static bool ESP01S_WaitAck(char *ack, uint32_t timeout);//等待指定应答字符串
static bool ESP01S_WaitMultiAck(char *ack, uint32_t timeout);//等待多个应答字符串
static bool ESP01S_SendCmd(char *cmd, char *ack, uint32_t timeout);//发送AT指令并等待应答

/**
  *@brief  ESP-01S初始化
  *@param  无
  *@retval 无
  */
void ESP01S_Init(void)
{
    //初始化串口1用于ESP-01S通信
    Serial1_Init();
    //延时等待模块上电稳定
    Delay_ms(3000);
    Serial2_Printf("[ESP01S] 初始化完成\r\n");
}

/**
  *@brief  清空ESP-01S接收缓冲区
  *@param  无
  *@retval 无
  */
void ESP01S_ClearRxBuffer(void)
{
    Serial1_ClearRxBuffer();
}

/**
  *@brief  等待指定应答字符串
  *@param  ack 期待的应答字符串
  *@param  timeout 超时时间单位ms
  *@retval true-成功收到应答 false-超时或收到ERROR
  */
static bool ESP01S_WaitAck(char *ack, uint32_t timeout)
{
    uint32_t startTime;
    uint32_t elapsedTime = 0;
    
    startTime = Timer_GetTick();
    
    while(elapsedTime < timeout)
    {
        //检查是否接收到完整帧
        if(ESP01S_RX_FLAG)
        {
            //添加字符串结束符
            ESP01S_RX_BUF[ESP01S_RX_COUNT] = '\0';
            
            //检查是否包含期待的应答字符串
            if(strstr((char*)ESP01S_RX_BUF, ack) != NULL)
            {
                return true;
            }
            
            //检查是否包含ERROR
            if(strstr((char*)ESP01S_RX_BUF, "ERROR") != NULL)
            {
                return false;
            }
        }
        
        elapsedTime = Timer_GetTick() - startTime;
        Delay_ms(1);
    }
    
    return false;
}

/**
  *@brief  等待多个可能的应答字符串
  *@param  ack 期待的应答字符串可为NULL
  *@param  timeout 超时时间单位ms
  *@retval true-成功收到应答 false-超时或收到ERROR/FAIL
  */
static bool ESP01S_WaitMultiAck(char *ack, uint32_t timeout)
{
    uint32_t startTime = Timer_GetTick();
    
    while((Timer_GetTick() - startTime) < timeout)
    {
        if(ESP01S_RX_FLAG)
        {
            ESP01S_RX_BUF[ESP01S_RX_COUNT] = '\0';
  
            //检查多个可能的成功标志
            if(strstr((char*)ESP01S_RX_BUF, "OK") != NULL ||
               strstr((char*)ESP01S_RX_BUF, "WIFI CONNECTED") != NULL ||
               strstr((char*)ESP01S_RX_BUF, "GOT IP") != NULL)
            {
                return true;
            }
            
            //检查失败标志
            if(strstr((char*)ESP01S_RX_BUF, "ERROR") != NULL ||
               strstr((char*)ESP01S_RX_BUF, "FAIL") != NULL)
            {
                return false;
            }
            
            //如果指定了特定应答也检查一下
            if(ack != NULL && strstr((char*)ESP01S_RX_BUF, ack) != NULL)
            {
                return true;
            }
        }
        Delay_ms(10);
    }
    return false;
}

/**
  *@brief  发送AT指令并等待应答
  *@param  cmd 要发送的指令
  *@param  ack 期待的应答字符串
  *@param  timeout 超时时间单位ms
  *@retval true-成功收到应答 false-超时或收到ERROR
  */
static bool ESP01S_SendCmd(char *cmd, char *ack, uint32_t timeout)
{
    //清空接收缓冲区避免历史数据干扰
    ESP01S_ClearRxBuffer();
    //发送指令AT指令必须以\r\n结尾
    Serial1_SendString(cmd);
    Serial1_SendString("\r\n");
    //等待应答
    return ESP01S_WaitAck(ack, timeout);
}

/**
  *@brief  AT测试检查模块是否正常
  *@param  无
  *@retval true-模块正常 false-模块异常
  */
bool ESP01S_AT_Test(void)
{
    uint8_t retry = 0;
    
    //最多重试5次
    while(retry < 5)
    {
        //发送AT指令期待返回OK
        if(ESP01S_SendCmd("AT", "OK", 500))
        {
            return true;
        }
        retry++;
        Delay_ms(1000);
    }
    
    return false;
}

/**
  *@brief  重启ESP-01S模块
  *@param  无
  *@retval true-重启成功 false-重启失败
  */
bool ESP01S_Restart(void)
{
    ESP01S_ClearRxBuffer();
    Serial1_SendString("AT+RST\r\n");
    
    if(!ESP01S_WaitAck("OK", 1000))
        return false;
    
    if(!ESP01S_WaitAck("ready", 5000))
        return false;
    
    Delay_ms(2000);
    return true;
}

/**
  *@brief  设置ESP-01S工作模式
  *@param  mode 工作模式1-Station 2-AP 3-Station+AP
  *@retval true-设置成功 false-设置失败
  */
bool ESP01S_SetMode(ESP01S_Mode_t mode)
{
    char cmd[20];
    
    sprintf(cmd, "AT+CWMODE=%d", mode);
    return ESP01S_SendCmd(cmd, "OK", 1000);
}

/**
  *@brief  设置单连接模式
  *@param  无
  *@retval true-设置成功 false-设置失败
  */
bool ESP01S_SetSingleConnection(void)
{
    return ESP01S_SendCmd("AT+CIPMUX=0", "OK", 1000);
}

/**
  *@brief  设置多连接模式
  *@param  无
  *@retval true-设置成功 false-设置失败
  */
bool ESP01S_SetMultiConnection(void)
{
    return ESP01S_SendCmd("AT+CIPMUX=1", "OK", 1000);
}

/**
  *@brief  连接配置的WiFi
  *@param  无
  *@retval true-连接成功 false-连接失败
  */
bool ESP01S_ConnectAP(void)
{
    char cmd[128];
    
    ESP01S_ClearRxBuffer();
    sprintf(cmd, "AT+CWJAP=\"%s\",\"%s\"", ESP01S_WIFI_SSID, ESP01S_WIFI_PASSWORD);
    Serial1_SendString(cmd);
    Serial1_SendString("\r\n");
    return ESP01S_WaitMultiAck(NULL, 3000);
}

/**
  *@brief  获取本机IP地址
  *@param  ipBuf 存储IP地址的缓冲区
  *@param  bufSize 缓冲区大小
  *@retval true-成功获取IP false-获取失败
  */
bool ESP01S_GetIP(char *ipBuf, uint8_t bufSize)
{
    char *pIpStart;
    char *pIpEnd;
    uint8_t ipLen;
    uint8_t retry = 0;
    
    while(retry < 3)
    {
        ESP01S_ClearRxBuffer();
        
        if(!ESP01S_SendCmd("AT+CIFSR", "OK", 3000))
        {
            retry++;
            Delay_ms(500);
            continue;
        }
        
        ESP01S_RX_BUF[ESP01S_RX_COUNT] = '\0';
        
        //查找IP地址格式+CIFSR:STAIP,"192.168.1.100"
        pIpStart = strstr((char*)ESP01S_RX_BUF, "+CIFSR:STAIP,\"");
        if(pIpStart == NULL)
        {
            pIpStart = strstr((char*)ESP01S_RX_BUF, "+CIFSR:STAIP,\"");
        }
        
        if(pIpStart != NULL)
        {
            pIpStart += 15;//跳过+CIFSR:STAIP,"
            pIpEnd = strstr(pIpStart, "\"");
            if(pIpEnd != NULL)
            {
                ipLen = pIpEnd - pIpStart;
                if(ipLen > 0 && ipLen < bufSize)
                {
                    strncpy(ipBuf, pIpStart, ipLen);
                    ipBuf[ipLen] = '\0';
                    return true;
                }
            }
        }
        
        retry++;
        Delay_ms(500);
    }
    
    return false;
}

/**
  *@brief  启动TCP服务器
  *@param  port 端口号字符串
  *@retval true-启动成功 false-启动失败
  */
bool ESP01S_StartServer(char *port)
{
    char cmd[30];
    
    ESP01S_ClearRxBuffer();
    sprintf(cmd, "AT+CIPSERVER=1,%s", port);
    return ESP01S_SendCmd(cmd, "OK", 2000);
}

/**
  *@brief  发送数据到客户端
  *@param  id 连接ID多连接模式使用0-4
  *@param  data 要发送的数据
  *@param  len 数据长度
  *@retval true-发送成功 false-发送失败
  */
bool ESP01S_SendData(ESP01S_ID_t id, char *data, uint16_t len)
{
    char cmd[30];
    uint8_t retry = 0;
    
    while(retry < 3)
    {
        ESP01S_ClearRxBuffer();
        
        //多连接模式使用AT+CIPSEND=<id>,<len>
        sprintf(cmd, "AT+CIPSEND=%d,%d", id, len);
        
        Serial2_Printf("[ESP01S] 发送指令: %s\r\n", cmd);
        
        //发送数据长度指令
        Serial1_SendString(cmd);
        Serial1_SendString("\r\n");
        
        //等待>提示符表示模块准备接收数据
        if(!ESP01S_WaitAck(">", 3000))
        {
            Serial2_Printf("[ESP01S] 等待>超时\r\n");
            retry++;
            Delay_ms(500);
            continue;
        }
        
        Serial2_Printf("[ESP01S] 收到>，准备发送数据: %s", data);
        
        //清空接收缓冲区准备接收发送结果
        ESP01S_ClearRxBuffer();
        
        //发送实际数据
        Serial1_SendString(data);
        
        //等待SEND OK响应表示数据发送成功
        if(ESP01S_WaitAck("SEND OK", 5000))
        {
            Serial2_Printf("[ESP01S] 数据发送成功\r\n");
            return true;
        }
        
        Serial2_Printf("[ESP01S] 等待SEND OK超时\r\n");
        retry++;
        Delay_ms(500);
    }
    
    Serial2_Printf("[ESP01S] 数据发送失败，重试%d次\r\n", retry);
    return false;
}

/**
  *@brief  从客户端接收数据
  *@param  id 连接ID
  *@param  buffer 接收数据缓冲区
  *@param  bufferSize 缓冲区大小
  *@retval 实际接收到的数据长度
  */
uint16_t ESP01S_ReceiveData(ESP01S_ID_t id, uint8_t *buffer, uint16_t bufferSize)
{
    char *pDataStart;
    char *pDataEnd;
    uint16_t dataLen = 0;
    
    if(!ESP01S_RX_FLAG)
    {
        return 0;
    }
    
    ESP01S_RX_BUF[ESP01S_RX_COUNT] = '\0';
    
    //查找IPD数据格式: +IPD,<id>,<len>:<data>
    pDataStart = strstr((char*)ESP01S_RX_BUF, "+IPD,");
    
    if(pDataStart != NULL)
    {
        //查找冒号分隔符
        pDataStart = strchr(pDataStart, ':');
        if(pDataStart != NULL)
        {
            pDataStart++;//跳过冒号
            //查找数据结束位置（换行符）
            pDataEnd = strstr(pDataStart, "\r\n");
            if(pDataEnd == NULL)
            {
                pDataEnd = strstr(pDataStart, "\n");
            }
            
            if(pDataEnd != NULL)
            {
                dataLen = pDataEnd - pDataStart;
                if(dataLen > 0 && dataLen < bufferSize)
                {
                    memcpy(buffer, pDataStart, dataLen);
                    buffer[dataLen] = '\0';
                }
            }
        }
    }
    
    //清空缓冲区，避免重复处理
    ESP01S_ClearRxBuffer();
    
    return dataLen;
}

/**
  *@brief  获取连接状态
  *@param  无
  *@retval 状态值2-获得IP 3-已建立连接 4-断开连接 0-获取失败
  */
uint8_t ESP01S_GetConnectionStatus(void)
{
    ESP01S_ClearRxBuffer();
    Serial1_SendString("AT+CIPSTATUS\r\n");
    
    if(ESP01S_WaitAck("OK", 500))
    {
        ESP01S_RX_BUF[ESP01S_RX_COUNT] = '\0';
        
        if(strstr((char*)ESP01S_RX_BUF, "STATUS:2") != NULL)
            return 2;
        else if(strstr((char*)ESP01S_RX_BUF, "STATUS:3") != NULL)
            return 3;
        else if(strstr((char*)ESP01S_RX_BUF, "STATUS:4") != NULL)
            return 4;
    }
    return 0;
}
