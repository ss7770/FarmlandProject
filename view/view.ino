/* SmartFarm 视觉巡检：ESP32-S3-CAM 每 5 秒拍照上传 Flask
 * 板子：安信可 AiPi-CAM-D200（ESP32-S3 + OV2640）
 * Arduino IDE 设置：开发板=ESP32S3 Dev Module，PSRAM=OPI PSRAM
 */
#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include "arduino_secrets.h"

const char* SERVER_URL = "http://192.168.43.115:5000/api/upload_image";
// 文档 v1.4：默认每 5 分钟拍一张（省电省流量），演示时可临时改小
const uint32_t CAPTURE_INTERVAL_MS = 5UL * 60UL * 1000UL;
const char* BOUNDARY = "----SmartFarmBoundary7d1a2c";
uint32_t lastShot = 0;

// ★ 摄像头引脚：D200 官方引脚表没查到，先按 ESP32-S3 通用排布填写。
// 到货后若初始化报 0x104/0x105 错误，对照板子原理图只改这 16 个数即可。
#define CAM_PIN_PWDN  -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK  15
#define CAM_PIN_SIOD   4
#define CAM_PIN_SIOC   5
#define CAM_PIN_D7    16
#define CAM_PIN_D6    17
#define CAM_PIN_D5    18
#define CAM_PIN_D4    12
#define CAM_PIN_D3    10
#define CAM_PIN_D2     8
#define CAM_PIN_D1     9
#define CAM_PIN_D0    11
#define CAM_PIN_VSYNC  6
#define CAM_PIN_HREF   7
#define CAM_PIN_PCLK  13

void log(const char* tag, const String& msg) {
  Serial.print("["); Serial.print(millis() / 1000);
  Serial.print("s]["); Serial.print(tag); Serial.print("] ");
  Serial.println(msg);
}

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D0;  config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2;  config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4;  config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6;  config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk   = CAM_PIN_XCLK;
  config.pin_sccb_sda = CAM_PIN_SIOD;   // 老版本核心叫 pin_sscb_sda
  config.pin_sccb_scl = CAM_PIN_SIOC;   // 老版本核心叫 pin_sscb_scl
  config.pin_vsync  = CAM_PIN_VSYNC;
  config.pin_href   = CAM_PIN_HREF;
  config.pin_pclk   = CAM_PIN_PCLK;
  config.pin_pwdn   = CAM_PIN_PWDN;
  config.pin_reset  = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_SVGA;  // 800x600，压缩后约 20~40KB
  config.jpeg_quality = 12;
  config.fb_count     = 1;
  config.grab_mode    = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) { log("CAM", "init failed err=0x" + String(err, HEX)); return false; }
  log("CAM", "init OK");
  return true;
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(SECRET_SSID, SECRET_PASS);
  log("WiFi", String("connecting to ") + SECRET_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(300); Serial.print('.'); }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) log("WiFi", "connected, IP=" + WiFi.localIP().toString());
  else log("WiFi", "TIMEOUT, will retry in loop()");
}

int uploadPhoto(camera_fb_t* fb) {
  String prefix = String("--") + BOUNDARY +
    "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"cap.jpg\"\r\n"
    "Content-Type: image/jpeg\r\n\r\n";
  String suffix = String("\r\n--") + BOUNDARY + "--\r\n";
  size_t total = prefix.length() + fb->len + suffix.length();

  uint8_t* buf = (uint8_t*)ps_malloc(total);      // 优先用 PSRAM
  if (!buf) buf = (uint8_t*)malloc(total);
  if (!buf) { log("UP", "no mem " + String(total)); return -1; }
  memcpy(buf, prefix.c_str(), prefix.length());
  memcpy(buf + prefix.length(), fb->buf, fb->len);
  memcpy(buf + prefix.length() + fb->len, suffix.c_str(), suffix.length());

  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(10000);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + BOUNDARY);
  int code = http.POST(buf, total);
  http.end();
  free(buf);
  return code;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  log("SYS", "SmartFarm ESP32-S3-CAM boot");
  log("SYS", psramFound() ? "PSRAM OK" : "PSRAM NOT found(请在IDE开启)");
  if (!initCamera()) { delay(5000); ESP.restart(); }
  connectWiFi();
  lastShot = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    log("WiFi", "lost, reconnecting...");
    WiFi.disconnect(); connectWiFi(); return;
  }
  if (millis() - lastShot < CAPTURE_INTERVAL_MS) { delay(50); return; }
  lastShot = millis();

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { log("CAM", "fb_get failed, skip"); return; }
  log("CAM", "captured " + String(fb->len) + " bytes");

  int code = uploadPhoto(fb);
  esp_camera_fb_return(fb);

  if (code > 0) log("UP", "HTTP " + String(code) + (code == 200 ? " 上传成功" : " 服务器报错"));
  else          log("UP", "upload FAILED err=" + String(code));
}
