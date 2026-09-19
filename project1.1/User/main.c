#include "stm32f10x.h"
#include "Delay.h"
#include "OLED.h"
#include "Timer.h"
#include "ADCX.h"
#include "DHT11.h"
#include "ESP01S.h"
#include "Serial.h"
#include "LDR.h"
#include "TS.h"
#include "water.h"
#include "RELAY.h"
#include "Buzzer.h"
#include "Filter.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>


/*注意：1.每次连接后的IP地址并不一定一致。
 *      3.WiFi名和密码见ESP01S.h，请把你自己的WiFi名和密码改成代码里的以便硬件能连接上WiFi。
 *      4.ESP-01S作为TCP服务器运行，端口号固定为8288。
 */

//显示模式枚举
typedef enum
{
    DISPLAY_MODE_NORMAL = 0//正常模式显示传感器数据
}DisplayMode_t;

//WiFi连接状态标志
static uint8_t g_WiFiConnected = 0;//WiFi连接状态1表示已连接
static uint8_t g_ServerStarted = 0;//服务器启动状态1表示已启动
static uint8_t g_ClientConnected = 0;//客户端连接标志1表示有客户端连接
/*
//传感器数据结构体
typedef struct
{
    uint8_t temp;//温度值
    uint8_t humi;//湿度值
    uint16_t lightLux;//光照强度值
    uint16_t soilHumidity;//土壤湿度百分比
    uint16_t waterLevel;//水位ADC值
}SensorData_t;
*/
static SensorData_t g_SensorData = {0};//全局传感器数据
static DisplayMode_t g_DisplayMode = DISPLAY_MODE_NORMAL;//当前显示模式

//数据发送缓冲区
static char g_DataBuffer[256];//数据发送缓冲区
//命令接收缓冲区
static char g_CmdBuffer[128];//命令接收缓冲区
static uint8_t g_CmdIndex = 0;//命令缓冲区索引

//阈值变更后需要立即重新检查报警的标志
static uint8_t g_NeedRecheckAlert = 0;

/* ==================== 灌溉安全控制（P0 重构） ====================
 * AI/APP 只有建议权，本层持有安全否决权：
 * - 滞回控制：低于启动阈值开泵，达到停止阈值才关泵，消除抖动启停
 * - 水位联锁：水位低于 waterMin 强制闭锁/停止水泵（防干转，最高优先级）
 * - 最小运行时间：开泵后至少运行 minPumpRunTimeMs 才允许正常停止
 * - 最长运行时间：超过 maxPumpRunTimeMs 强制停止（异常保护）
 * - 最小停机间隔：两次灌溉之间至少间隔 minPumpStopTimeMs
 */
typedef enum
{
    IRR_STATE_IDLE = 0,    //待机：检查是否满足启动条件
    IRR_STATE_RUNNING = 1  //灌溉中：检查停止/保护条件
} IrrState_t;

typedef struct
{
    uint16_t soilStartThreshold;    //土壤湿度低于此值(%)启动灌溉
    uint16_t soilStopThreshold;     //土壤湿度达到此值(%)停止灌溉（滞回）
    uint16_t waterMin;              //水位安全下限(ADC)，低于则闭锁水泵
    uint32_t minPumpRunTimeMs;      //水泵最小运行时间
    uint32_t maxPumpRunTimeMs;      //水泵最长连续运行时间
    uint32_t minPumpStopTimeMs;     //两次灌溉最小停机间隔
} IrrigationConfig_t;

static IrrigationConfig_t g_IrrConfig = {
    10,        //soilStartThreshold：默认低于10%启动（与原逻辑兼容，可被APP修改）
    20,        //soilStopThreshold：默认达到20%停止（滞回，计划书承诺值）
    1000,      //waterMin：水位低于1000(ADC)闭锁水泵（计划书承诺值）
    30000,     //minPumpRunTimeMs：最少运行30秒
    120000,    //maxPumpRunTimeMs：最长连续运行120秒
    30000      //minPumpStopTimeMs：两次灌溉至少间隔30秒
};
static IrrState_t g_IrrState = IRR_STATE_IDLE;//灌溉状态机
static uint32_t g_PumpStartTime = 0;//本次开泵时刻
static uint32_t g_PumpLastStopTime = 0;//上次停泵时刻

/**
*@brief  灌溉安全控制任务（每200ms调用一次）
*@param  无
*@retval 无
*/
static void IrrigationControlTask(void)
{
    uint32_t now = Timer_GetTick();

    switch(g_IrrState)
    {
        case IRR_STATE_IDLE://待机：泵必须保持断开
            Relay_Control(0);

            //水位联锁：水位不足时无条件闭锁启动
            if(g_SensorData.waterLevel < g_IrrConfig.waterMin)
            {
                break;//闭锁中，什么都不做
            }

            //停机间隔保护：距上次停泵不足最间隔不允许启动
            if((now - g_PumpLastStopTime) < g_IrrConfig.minPumpStopTimeMs)
            {
                break;
            }

            //滞回启动：土壤湿度低于启动阈值
            if(g_SensorData.soilHumidity < g_IrrConfig.soilStartThreshold)
            {
                Relay_Control(1);
                g_PumpStartTime = now;
                g_IrrState = IRR_STATE_RUNNING;
                Serial2_Printf("[PUMP] START - Soil:%d%% < %d%%, Water:%d\r\n",
                    g_SensorData.soilHumidity, g_IrrConfig.soilStartThreshold,
                    g_SensorData.waterLevel);
            }
            break;

        case IRR_STATE_RUNNING://灌溉中
            //最高优先级：运行中水位跌破安全值，立即停泵（干转保护）
            if(g_SensorData.waterLevel < g_IrrConfig.waterMin)
            {
                Relay_Control(0);
                g_PumpLastStopTime = now;
                g_IrrState = IRR_STATE_IDLE;
                Serial2_Printf("[PUMP] EMERGENCY STOP - Water:%d < %d (dry-run protection)\r\n",
                    g_SensorData.waterLevel, g_IrrConfig.waterMin);
                break;
            }

            //最长运行保护：超时强制停泵
            if((now - g_PumpStartTime) >= g_IrrConfig.maxPumpRunTimeMs)
            {
                Relay_Control(0);
                g_PumpLastStopTime = now;
                g_IrrState = IRR_STATE_IDLE;
                Serial2_Printf("[PUMP] STOP - max run time %dms reached\r\n",
                    g_IrrConfig.maxPumpRunTimeMs);
                break;
            }

            //正常停止：湿度达到停止阈值（滞回），且已满足最小运行时间
            if(g_SensorData.soilHumidity >= g_IrrConfig.soilStopThreshold)
            {
                if((now - g_PumpStartTime) >= g_IrrConfig.minPumpRunTimeMs)
                {
                    Relay_Control(0);
                    g_PumpLastStopTime = now;
                    g_IrrState = IRR_STATE_IDLE;
                    Serial2_Printf("[PUMP] STOP - Soil:%d%% >= %d%%\r\n",
                        g_SensorData.soilHumidity, g_IrrConfig.soilStopThreshold);
                }
                //未满足最小运行时间则继续运行（计划书：最少运行30秒）
            }
            break;

        default:
            g_IrrState = IRR_STATE_IDLE;
            break;
    }
}
/* ==================== 灌溉安全控制结束 ==================== */

/**
*@brief  格式化传感器数据为字符串
*@param  data 传感器数据指针
*@param  buffer 输出缓冲区指针
*@param  bufferSize 缓冲区大小
*@retval 生成的字符串长度
*/
static int FormatSensorDataToString(SensorData_t *data, char *buffer, int bufferSize)
{
    //构建简化格式字符串上位机更容易解析
    //格式: TEMP:25,HUMI:60,LIGHT:500,SOIL:80,WATER:2048\n
    return snprintf(buffer, bufferSize,
        "TEMP:%d,HUMI:%d,LIGHT:%d,SOIL:%d,WATER:%d\n",
        data->temp,
        data->humi,
        data->lightLux,
        data->soilHumidity,
        data->waterLevel);
}

/**
*@brief  发送传感器数据到所有连接的客户端
*@param  无
*@retval 无
*/
static void SendSensorDataToClient(void)
{
    int dataLen;
    uint8_t sendSuccess = 0;

    //如果没有客户端连接直接返回
    if(g_ClientConnected == 0)
    {
        Serial2_Printf("[INFO] No client connected, skip sending\r\n");
        return;
    }

    //格式化传感器数据为字符串
    dataLen = FormatSensorDataToString(&g_SensorData, g_DataBuffer, sizeof(g_DataBuffer));

    if(dataLen > 0 && dataLen < sizeof(g_DataBuffer))
    {
        Serial2_Printf("[SEND] Ready to send data: %s\r\n", g_DataBuffer);
        
        //重试3次发送
        for(uint8_t id = 0; id <= 4; id++)
        {
            if(ESP01S_SendData((ESP01S_ID_t)id, g_DataBuffer, dataLen))
            {
                sendSuccess = 1;
                Serial2_Printf("[OK] Data sent to ID %d\r\n", id);
            }
        }
        
        if(sendSuccess == 0)
        {
            Serial2_Printf("[ERROR] Failed to send sensor data, client may be disconnected\r\n");
            g_ClientConnected = 0;//标记客户端已断开
        }
    }
    else
    {
        Serial2_Printf("[ERROR] Data formatting failed, dataLen=%d\r\n", dataLen);
    }
}

/**
*@brief  解析上位机发送的阈值设置命令
*@param  cmd 命令字符串
*@retval 无
*/
static void ParseThresholdCommand(char *cmd)
{
    char *pToken;
    char *pSavePtr;
    char key[16];
    int value;
    uint8_t thresholdChanged = 0;

    Serial2_Printf("[CMD] Parsing threshold command: %s\r\n", cmd);

    //跳过"SETTHRESHOLD:"前缀
    if(strstr(cmd, "SETTHRESHOLD:") != NULL)
    {
        cmd = strstr(cmd, "SETTHRESHOLD:") + 13;
    }
    else
    {
        Serial2_Printf("[CMD] Invalid command format, missing SETTHRESHOLD:\r\n");
        return;
    }

    //按逗号分割各个参数
    pToken = strtok_r(cmd, ",", &pSavePtr);
    while(pToken != NULL)
    {
        char *pColon = strchr(pToken, ':');
        if(pColon != NULL)
        {
            *pColon = '\0';
            strcpy(key, pToken);
            value = atoi(pColon + 1);

            if(strcmp(key, "TEMP") == 0)
            {
                if((uint8_t)value != Buzzer_GetTempThreshold())
                {
                    Buzzer_SetTempThreshold((uint8_t)value);
                    thresholdChanged = 1;
                    Serial2_Printf("[CMD] Temperature threshold set to: %d°C\r\n", value);
                }
            }
            else if(strcmp(key, "HUMI") == 0)
            {
                if((uint8_t)value != Buzzer_GetHumiThreshold())
                {
                    Buzzer_SetHumiThreshold((uint8_t)value);
                    thresholdChanged = 1;
                    Serial2_Printf("[CMD] Humidity threshold set to: %d%%\r\n", value);
                }
            }
            else if(strcmp(key, "LIGHT") == 0)
            {
                if((uint16_t)value != Buzzer_GetLightThreshold())
                {
                    Buzzer_SetLightThreshold((uint16_t)value);
                    thresholdChanged = 1;
                    Serial2_Printf("[CMD] Light threshold set to: %d Lux\r\n", value);
                }
            }
            else if(strcmp(key, "SOIL") == 0)
            {
                //土壤湿度阈值：真正写入灌溉配置并参与水泵控制
                if(value >= 1 && value <= 90)
                {
                    g_IrrConfig.soilStartThreshold = (uint16_t)value;
                    //滞回：停止阈值自动设为启动阈值+10，保证有迟滞区间
                    g_IrrConfig.soilStopThreshold = (uint16_t)(value + 10);
                    if(g_IrrConfig.soilStopThreshold > 100)
                    {
                        g_IrrConfig.soilStopThreshold = 100;
                    }
                    Serial2_Printf("[CMD] Soil start threshold: %d%%, stop(hysteresis): %d%%\r\n",
                        g_IrrConfig.soilStartThreshold, g_IrrConfig.soilStopThreshold);
                }
                else
                {
                    Serial2_Printf("[CMD] Soil threshold %d out of range(1-90), rejected\r\n", value);
                }
            }
            else if(strcmp(key, "WATER") == 0)
            {
                if((uint16_t)value != Buzzer_GetWaterThreshold())
                {
                    Buzzer_SetWaterThreshold((uint16_t)value);
                    thresholdChanged = 1;
                    Serial2_Printf("[CMD] Water threshold set to: %d ADC (water < %d will alarm)\r\n", value, value);
                }
            }
            else
            {
                Serial2_Printf("[CMD] Unknown key: %s\r\n", key);
            }
        }
        else
        {
            Serial2_Printf("[CMD] Invalid key-value pair: %s\r\n", pToken);
        }
        pToken = strtok_r(NULL, ",", &pSavePtr);
    }

    //打印所有当前阈值，便于调试确认蜂鸣器使用的阈值已更新
    Serial2_Printf("[CMD] Current thresholds - Temp:%d°C, Humi:%d%%, Light:%dLux, Water:%dADC (alarm when water < %d)\r\n",
        Buzzer_GetTempThreshold(), Buzzer_GetHumiThreshold(),
        Buzzer_GetLightThreshold(), Buzzer_GetWaterThreshold(), Buzzer_GetWaterThreshold());
    Serial2_Printf("[CMD] Irrigation config - SoilStart:%d%%, SoilStop:%d%%, WaterMin:%d, MinRun:%dms, MaxRun:%dms, MinStop:%dms\r\n",
        g_IrrConfig.soilStartThreshold, g_IrrConfig.soilStopThreshold, g_IrrConfig.waterMin,
        g_IrrConfig.minPumpRunTimeMs, g_IrrConfig.maxPumpRunTimeMs, g_IrrConfig.minPumpStopTimeMs);

    //阈值发生变化，立即重新检查报警状态
    if(thresholdChanged)
    {
        g_NeedRecheckAlert = 1;
        Serial2_Printf("[CMD] Threshold changed, will recheck alert status immediately\r\n");
    }
}

/**
*@brief  处理从上位机接收到的数据
*@param  data 接收到的数据
*@param  len 数据长度
*@retval 无
*/
static void ProcessReceivedData(uint8_t *data, uint16_t len)
{
    uint16_t i;
    
    Serial2_Printf("[RECV] Processing %d bytes: %s\r\n", len, data);
    
    for(i = 0; i < len && g_CmdIndex < sizeof(g_CmdBuffer) - 1; i++)
    {
        char ch = (char)data[i];
        
        if(ch == '\n' || ch == '\r')
        {
            if(g_CmdIndex > 0)
            {
                g_CmdBuffer[g_CmdIndex] = '\0';
                Serial2_Printf("[CMD] Received complete command: %s\r\n", g_CmdBuffer);
                
                //检查是否为阈值设置命令
                if(strstr(g_CmdBuffer, "SETTHRESHOLD") != NULL)
                {
                    ParseThresholdCommand(g_CmdBuffer);
                }
                
                g_CmdIndex = 0;
                memset(g_CmdBuffer, 0, sizeof(g_CmdBuffer));
            }
        }
        else if(ch >= 32 && ch < 127)//可打印字符
        {
            g_CmdBuffer[g_CmdIndex++] = ch;
        }
    }
    
    if(g_CmdIndex >= sizeof(g_CmdBuffer) - 1)
    {
        g_CmdIndex = 0;
        memset(g_CmdBuffer, 0, sizeof(g_CmdBuffer));
    }
}

/**
*@brief  显示正常模式的静态标签
*@param  无
*@retval 无
*/
static void OLED_ShowNormalStaticLabels(void)
{
    OLED_ShowChinese(1, 1, 0);//温
    OLED_ShowChinese(1, 2, 1);//度
    OLED_ShowString(1, 5, ":");
    OLED_ShowChinese(1, 5, 2);//湿
    OLED_ShowChinese(1, 6, 1);//度
    OLED_ShowString(1, 13, ":");

    OLED_ShowChinese(2, 1, 3);//光
    OLED_ShowChinese(2, 2, 4);//照
    OLED_ShowChinese(2, 3, 5);//强
    OLED_ShowChinese(2, 4, 1);//度
    OLED_ShowString(2, 9, ":");

    OLED_ShowChinese(3, 1, 6);//土
    OLED_ShowChinese(3, 2, 7);//壤
    OLED_ShowChinese(3, 3, 2);//湿
    OLED_ShowChinese(3, 4, 1);//度
    OLED_ShowString(3, 9, ":");

    OLED_ShowChinese(4, 1, 8);//水
    OLED_ShowChinese(4, 2, 9);//位
    OLED_ShowString(4, 5, ":");
}

/**
*@brief  恢复正常显示模式
*@param  无
*@retval 无
*/
static void OLED_RestoreNormalDisplay(void)
{
    OLED_Clear();
    OLED_ShowNormalStaticLabels();

    OLED_ShowNum(1, 6, g_SensorData.temp, 2);
    OLED_ShowNum(1, 14, g_SensorData.humi, 2);
    
    //显示光照强度
    OLED_ShowNum(2, 10, g_SensorData.lightLux, 3);
    
    //显示土壤湿度
    OLED_ShowNum(3, 10, g_SensorData.soilHumidity, 3);
    OLED_ShowChar(3, 13, '%');
    
    //显示水位ADC值
    OLED_ShowNum(4, 6, g_SensorData.waterLevel, 4);

    if(g_WiFiConnected)
    {
        OLED_ShowChar(4, 16, 'Y');
    }
    else
    {
        OLED_ShowChar(4, 16, 'N');
    }
}

/**
*@brief  更新OLED显示
*@param  无
*@retval 无
*/
static void OLED_UpdateDisplay(void)
{
    if(g_DisplayMode == DISPLAY_MODE_NORMAL)
    {
        OLED_ShowNum(1, 6, g_SensorData.temp, 2);
        OLED_ShowNum(1, 14, g_SensorData.humi, 2);
        
        //更新光照强度
        OLED_ShowNum(2, 10, g_SensorData.lightLux, 3);
        
        //更新土壤湿度
        OLED_ShowNum(3, 10, g_SensorData.soilHumidity, 3);
        
        //更新水位ADC值
        OLED_ShowNum(4, 6, g_SensorData.waterLevel, 4);
        
        if(g_WiFiConnected)
        {
            OLED_ShowChar(4, 16, 'Y');
        }
        else
        {
            OLED_ShowChar(4, 16, 'N');
        }
    }
}

/**
*@brief  读取所有传感器数据
*@param  data 传感器数据指针
*@retval 无
*/
static void ReadAllSensors(SensorData_t *data)
{
    uint8_t dht11ReadSuccess = 0;

    //读取温湿度数据
    dht11ReadSuccess = DHT11_ReadData(&data->humi, &data->temp);
    if(dht11ReadSuccess == 0)
    {
        data->temp = 25;//测试值
        data->humi = 50;//测试值
    }
    else{}
    
    //读取光照强度
    data->lightLux = LDR_LuxData();
    
    //读取土壤湿度
    data->soilHumidity = TS_GetData();
    
    //读取水位传感器
    data->waterLevel = Water_GetData();
			
		// 新增1：对读取到的数据进行滤波处理
		Filter_SensorData(data);
}

/**
*@brief  检查报警状态并控制蜂鸣器
*@param  无
*@retval 无
*/
static void CheckAndControlAlert(void)
{
    uint8_t shouldAlert = 0;
    uint8_t tempThreshold = Buzzer_GetTempThreshold();
    uint8_t humiThreshold = Buzzer_GetHumiThreshold();
    uint16_t lightThreshold = Buzzer_GetLightThreshold();
    uint16_t waterThreshold = Buzzer_GetWaterThreshold();

    //判断是否触发警报条件：温度超过阈值 或 湿度超过阈值 或 光照超过阈值 或 水位低于阈值
    if(g_SensorData.temp > tempThreshold || 
       g_SensorData.humi > humiThreshold || 
       g_SensorData.lightLux > lightThreshold || 
       g_SensorData.waterLevel < waterThreshold)

		{
        shouldAlert = 1;
        Serial2_Printf("[ALERT] Alert triggered - Temp:%d>T:%d?%d, Humi:%d>H:%d?%d, Light:%d>L:%d?%d, Water:%d<W:%d?%d\r\n",
            g_SensorData.temp, tempThreshold, (g_SensorData.temp > tempThreshold),
            g_SensorData.humi, humiThreshold, (g_SensorData.humi > humiThreshold),
            g_SensorData.lightLux, lightThreshold, (g_SensorData.lightLux > lightThreshold),
            g_SensorData.waterLevel, waterThreshold, (g_SensorData.waterLevel < waterThreshold));
    }
    else
    {
        Serial2_Printf("[ALERT] No alert - Temp:%d, Humi:%d, Light:%d, Water:%d, Thresholds - T:%d, H:%d, L:%d, W:%d\r\n",
            g_SensorData.temp, g_SensorData.humi, g_SensorData.lightLux, g_SensorData.waterLevel,
            tempThreshold, humiThreshold, lightThreshold, waterThreshold);
    }

    //控制蜂鸣器
    if(shouldAlert)
    {
        Buzzer_On();
    }
    else
    {
        Buzzer_Off();
    }
}

/**
*@brief  WiFi连接和服务器启动任务
*@param  无
*@retval 无
*/
static void WiFi_ServerTask(void)
{
    static uint8_t connectState = 0;//连接状态机
    static uint8_t retryCount = 0;//重试次数
    char ipBuf[20] = {0};//IP地址缓冲区

    //如果服务器已经启动成功不再重复操作
    if(g_ServerStarted == 1)
    {
        return;
    }

    //连接状态机
    switch(connectState)
    {
        case 0://初始化开始连接
            connectState = 1;
            break;

        case 1://AT测试检查模块是否正常
            if(ESP01S_AT_Test())
            {
                connectState = 2;
                Serial2_Printf("[INFO] AT test success\r\n");
            }
            else
            {
                retryCount++;
                if(retryCount >= 3)
                {
                    retryCount = 0;
                    connectState = 0;
                }
            }
            break;

        case 2://设置工作模式为Station模式
            if(ESP01S_SetMode(ESP01S_MODE_STA))
            {
                connectState = 3;
                Serial2_Printf("[INFO] Set Station mode success\r\n");
            }
            else
            {
                connectState = 0;
            }
            break;

        case 3://连接WiFi热点
            Serial2_Printf("[INFO] Connecting to WiFi...\r\n");
            if(ESP01S_ConnectAP())
            {
                g_WiFiConnected = 1;
                connectState = 4;
                Serial2_Printf("[INFO] WiFi connected successfully\r\n");
            }
            else
            {
                connectState = 0;
                retryCount = 0;
                Serial2_Printf("[ERROR] WiFi connection failed\r\n");
            }
            break;

        case 4://获取IP地址
            if(ESP01S_GetIP(ipBuf, sizeof(ipBuf)))
            {
                Serial2_Printf("[INFO] Local IP address: %s\r\n", ipBuf);
                connectState = 5;
                retryCount = 0;
            }
            else
            {
                connectState = 0;
            }
            break;

        case 5://设置多连接模式
            if(ESP01S_SetMultiConnection())
            {
                connectState = 6;
                Serial2_Printf("[INFO] Set multi-connection mode success\r\n");
            }
            else
            {
                connectState = 0;
            }
            break;

        case 6://启动TCP服务器
            if(ESP01S_StartServer((char*)ESP01S_SERVER_PORT))
            {
                g_ServerStarted = 1;
                connectState = 7;
                Serial2_Printf("[INFO] TCP server started on port: %s\r\n", ESP01S_SERVER_PORT);
            }
            else
            {
                connectState = 0;
            }
            break;

        case 7://已就绪无需操作
        default:
            break;
    }
}

/**
*@brief  检查客户端连接状态并接收数据
*@param  无
*@retval 无
*/
static void CheckClientConnection(void)
{
    uint8_t status;
    uint8_t rxData[256];
    uint16_t rxLen;

    //查询连接状态
    status = ESP01S_GetConnectionStatus();

    //STATUS:2表示已获得IP STATUS:3表示已建立连接 STATUS:4表示已断开连接
    if(status == 3)//已建立连接
    {
        if(g_ClientConnected == 0)//之前没有客户端连接
        {
            g_ClientConnected = 1;
            Serial2_Printf("[INFO] Client connected\r\n");
            //客户端连接后立即发送一次数据
            SendSensorDataToClient();
        }
        
        //检查是否有数据从客户端接收
        rxLen = ESP01S_ReceiveData(ESP01S_ID_0, rxData, sizeof(rxData));
        if(rxLen > 0)
        {
            Serial2_Printf("[RECV] Received %d bytes from client\r\n", rxLen);
            ProcessReceivedData(rxData, rxLen);
        }
    }
    else if(status == 4)//已断开连接
    {
        if(g_ClientConnected == 1)//之前有客户端连接现在断开
        {
            g_ClientConnected = 0;
            g_ServerStarted = 0;//自动重连
            Serial2_Printf("[INFO] Client disconnected\r\n");
        }
    }
}

int main(void)
{
    static uint32_t lastSensorReadTime = 0;//上次读取传感器的时间
    static uint32_t lastWiFiTaskTime = 0;//上次WiFi任务执行时间
    static uint32_t lastSendDataTime = 0;//上次发送数据的时间

    //设置中断优先级分组
    NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);

    //初始化各模块
    OLED_Init();//OLED初始化
    Timer_Init();//定时器初始化
    ADCX_Init();//ADC初始化
    DHT11_Init();//DHT11温湿度传感器初始化
    LDR_Init();//光敏传感器初始化
    TS_Init();//土壤湿度传感器初始化
    Water_Init();//水位传感器初始化
    Relay_Init();//继电器初始化
    Buzzer_Init();//蜂鸣器初始化
    Serial2_Init();//调试串口初始化
    ESP01S_Init();//ESP-01S WiFi模块初始化

    //打印初始阈值信息
    Serial2_Printf("[INFO] Initial thresholds - Temp:%d°C, Humi:%d%%, Light:%dLux, Water:%dADC (alarm when water < %d)\r\n",
        Buzzer_GetTempThreshold(), Buzzer_GetHumiThreshold(),
        Buzzer_GetLightThreshold(), Buzzer_GetWaterThreshold(), Buzzer_GetWaterThreshold());

    //OLED屏幕初始化界面
    OLED_RestoreNormalDisplay();

    //立即读取一次传感器数据显示
    ReadAllSensors(&g_SensorData);
    OLED_UpdateDisplay();
    //初始报警状态检查
    CheckAndControlAlert();

    while(1)
    {
        //每200ms读取一次传感器数据
        if((Timer_GetTick() - lastSensorReadTime) >= 200)
        {
            lastSensorReadTime = Timer_GetTick();
            ReadAllSensors(&g_SensorData);

            //检查报警状态并控制蜂鸣器（使用当前已更新的阈值）
            CheckAndControlAlert();
            
            //灌溉安全控制（滞回+水位联锁+启停保护，替代旧的 soil<10 直控）
            IrrigationControlTask();

            OLED_UpdateDisplay();
        }

        //如果阈值变更标志被设置，立即重新检查报警状态
        if(g_NeedRecheckAlert)
        {
            g_NeedRecheckAlert = 0;
            Serial2_Printf("[INFO] Rechecking alert status due to threshold change\r\n");
            CheckAndControlAlert();
        }

        //每100ms处理一次WiFi任务
        if((Timer_GetTick() - lastWiFiTaskTime) >= 100)
        {
            lastWiFiTaskTime = Timer_GetTick();
            WiFi_ServerTask();
            CheckClientConnection();
        }

        //每1秒发送一次传感器数据到客户端
        if((Timer_GetTick() - lastSendDataTime) >= 1000)
        {
            lastSendDataTime = Timer_GetTick();
            
            if(g_ClientConnected == 1)
            {
                SendSensorDataToClient();
            }
            else{}
        }

        //短暂延时避免CPU占用过高
        Delay_ms(5);
    }
}
