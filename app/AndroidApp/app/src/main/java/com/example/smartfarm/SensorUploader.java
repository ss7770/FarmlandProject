package com.example.smartfarm;

import android.content.Context;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 传感器数据上报器（P1）：
 * - TCP 收到 STM32 传感器数据后，按节流间隔 POST /api/sensor 写入 Flask sensor.db
 * - 这是"传感器数据持续入 SQLite"的统一入口，同时为墒情预测积累训练数据
 * - 上报失败按退避间隔暂停尝试，恢复后自动继续，不影响 TCP 主链路
 */
public class SensorUploader {

    private static final String TAG = "SensorUploader";
    /** 节流：最快每 10 秒上报一次（STM32 每 1 秒发一次，无需全量入库） */
    private static final long UPLOAD_INTERVAL_MS = 10_000L;
    /** 失败退避：连续失败后 60 秒内不再尝试 */
    private static final long FAIL_BACKOFF_MS = 60_000L;

    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();

    private static long lastUploadAt = 0L;
    private static long lastFailAt = 0L;

    private SensorUploader() {
    }

    /** 在收到传感器数据时调用（主线程/任意线程均可，内部自行节流与异步） */
    public static void report(Context context, SensorData data) {
        if (context == null || data == null) return;
        long now = System.currentTimeMillis();
        if (now - lastFailAt < FAIL_BACKOFF_MS) return;          // 退避期
        if (now - lastUploadAt < UPLOAD_INTERVAL_MS) return;     // 节流期
        lastUploadAt = now;

        final Context app = context.getApplicationContext();
        EXECUTOR.execute(new Runnable() {
            @Override
            public void run() {
                try {
                    JSONObject body = new JSONObject();
                    body.put("temp", data.getTemperature());
                    body.put("humi", data.getHumidity());
                    body.put("light", data.getLightLux());
                    body.put("soil", data.getSoilHumidity());
                    body.put("water", data.getWaterLevel());
                    body.put("device", "android-app");

                    byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
                    HttpURLConnection conn = (HttpURLConnection)
                            new URL(ServerConfig.get(app) + "/api/sensor").openConnection();
                    conn.setRequestMethod("POST");
                    conn.setConnectTimeout(4000);
                    conn.setReadTimeout(4000);
                    conn.setDoOutput(true);
                    conn.setFixedLengthStreamingMode(payload.length);
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                    OutputStream os = conn.getOutputStream();
                    os.write(payload);
                    os.flush();
                    os.close();
                    int code = conn.getResponseCode();
                    conn.disconnect();
                    if (code == 200) {
                        lastFailAt = 0L;
                        Log.d(TAG, "sensor data uploaded");
                    } else {
                        lastFailAt = System.currentTimeMillis();
                        Log.w(TAG, "upload HTTP " + code);
                    }
                } catch (Exception e) {
                    lastFailAt = System.currentTimeMillis();
                    Log.w(TAG, "upload failed: " + e.getMessage());
                }
            }
        });
    }
}
