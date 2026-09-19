package com.example.smartfarm;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import android.content.DialogInterface;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends AppCompatActivity {
    private static final String TAG = "MainActivity";

    /** 断线自动重连间隔（毫秒） */
    private static final long RECONNECT_DELAY_MS = 5000L;
    /** 心跳超时：STM32 每 1 秒发一次数据，10 秒没收到视为连接已死 */
    private static final long DATA_TIMEOUT_MS = 10000L;
    /** 看板轮询周期 */
    private static final long WATCHDOG_PERIOD_MS = 5000L;

    private EditText etIp, etPort;
    private Button btnConnect, btnThreshold, btnDisease, btnMore, btnServer;
    private TextView tvStatus, tvTempValue, tvHumiValue, tvLightValue, tvSoilValue, tvWaterValue;

    private TcpClient tcpClient;
    private boolean isConnected = false;
    /** 用户主动点过"连接"且未主动断开时为 true——自动重连只在这种状态下生效 */
    private boolean userRequestedConnect = false;
    private String lastIp;
    private int lastPort;
    /** 最近一次收到传感器数据的时刻（elapsedRealtime），用于心跳判活 */
    private long lastDataTime;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    /** 自动重连任务 */
    private final Runnable reconnectRunnable = new Runnable() {
        @Override
        public void run() {
            if (!userRequestedConnect || isConnected) return;
            Log.i(TAG, "auto-reconnecting to " + lastIp + ":" + lastPort);
            tvStatus.setText(R.string.connecting);
            tvStatus.setTextColor(getResources().getColor(R.color.colorWarning));
            connectInternal();
        }
    };
    /** 连接看门狗：连接着却长时间收不到数据 → 强制断开触发重连 */
    private final Runnable watchdogRunnable = new Runnable() {
        @Override
        public void run() {
            if (isConnected
                    && SystemClock.elapsedRealtime() - lastDataTime > DATA_TIMEOUT_MS) {
                Log.w(TAG, "no data for " + DATA_TIMEOUT_MS + "ms, forcing reconnect");
                if (tcpClient != null) {
                    tcpClient.disconnect();   // 会回调 onDisconnected → 走自动重连
                }
            }
            if (userRequestedConnect) {
                mainHandler.postDelayed(this, WATCHDOG_PERIOD_MS);
            }
        }
    };

    /** 病害巡检轮询器（文档 v1.4）：30 秒轮询后端，新病害弹通知 + TTS 播报 */
    private DiseasePoller diseasePoller;

    private int tempThreshold = 30;
    private int humiThreshold = 50;
    private int lightThreshold = 800;
    private int waterThreshold = 1000;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        initViews();
        setupListeners();

        diseasePoller = new DiseasePoller(this, new DiseasePoller.Listener() {
            @Override
            public void onNewRecords(long newLastId, String diseaseLabel, double confidence) {
                Toast.makeText(MainActivity.this,
                        "巡检提醒：检测到病害，已通知", Toast.LENGTH_LONG).show();
            }
        });
        diseasePoller.start();
    }

    private void initViews() {
        etIp = findViewById(R.id.et_ip);
        etPort = findViewById(R.id.et_port);
        btnConnect = findViewById(R.id.btn_connect);
        btnThreshold = findViewById(R.id.btn_threshold);
        btnDisease = findViewById(R.id.btn_disease);
        btnMore = findViewById(R.id.btn_more);
        btnServer = findViewById(R.id.btn_server);
        tvStatus = findViewById(R.id.tv_status);
        tvTempValue = findViewById(R.id.tv_temp_value);
        tvHumiValue = findViewById(R.id.tv_humi_value);
        tvLightValue = findViewById(R.id.tv_light_value);
        tvSoilValue = findViewById(R.id.tv_soil_value);
        tvWaterValue = findViewById(R.id.tv_water_value);
    }

    private void setupListeners() {
        btnConnect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (isConnected) {
                    disconnect();
                } else {
                    connect();
                }
            }
        });

        btnThreshold.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showThresholdDialog();
            }
        });

        btnDisease.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, DiseaseActivity.class));
            }
        });

        // “更多”看板入口：WebView 打开 Flask 端 dashboard（天气/图表/巡检记录）
        btnMore.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, MoreActivity.class));
            }
        });

        // 服务器地址设置：换网络环境后在此修改 Flask 地址，无需改代码重新编译
        btnServer.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showServerDialog();
            }
        });
    }

    /** 弹窗修改 Flask 服务器地址（存 SharedPreferences，全 APP 生效） */
    private void showServerDialog() {
        LinearLayout dialogLayout = new LinearLayout(this);
        dialogLayout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        dialogLayout.setPadding(pad, pad, pad, 0);

        TextView hint = new TextView(this);
        hint.setText(R.string.server_settings_hint);
        hint.setTextSize(13);
        dialogLayout.addView(hint);

        final EditText etServer = new EditText(this);
        etServer.setSingleLine(true);
        etServer.setText(ServerConfig.get(this));
        dialogLayout.addView(etServer);

        new AlertDialog.Builder(this)
                .setTitle(R.string.server_settings)
                .setView(dialogLayout)
                .setPositiveButton(R.string.save, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        ServerConfig.set(MainActivity.this, etServer.getText().toString());
                        Toast.makeText(MainActivity.this,
                                R.string.server_settings_saved, Toast.LENGTH_SHORT).show();
                    }
                })
                .setNegativeButton(R.string.cancel, null)
                .show();
    }

    private void connect() {
        String ip = etIp.getText().toString().trim();
        String portStr = etPort.getText().toString().trim();

        if (TextUtils.isEmpty(ip)) {
            Toast.makeText(this, R.string.enter_ip, Toast.LENGTH_SHORT).show();
            return;
        }

        if (TextUtils.isEmpty(portStr)) {
            Toast.makeText(this, R.string.enter_port, Toast.LENGTH_SHORT).show();
            return;
        }

        int port = Integer.parseInt(portStr);

        lastIp = ip;
        lastPort = port;
        userRequestedConnect = true;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        mainHandler.postDelayed(watchdogRunnable, WATCHDOG_PERIOD_MS);

        tvStatus.setText(R.string.connecting);
        tvStatus.setTextColor(getResources().getColor(R.color.colorWarning));
        btnConnect.setEnabled(false);

        connectInternal();
    }

    /** 用记住的 ip/port 建立连接（手动连接和自动重连共用） */
    private void connectInternal() {
        tcpClient = new TcpClient(lastIp, lastPort);
        tcpClient.setConnectionListener(new TcpClient.OnConnectionListener() {
            @Override
            public void onConnected() {
                isConnected = true;
                lastDataTime = SystemClock.elapsedRealtime();
                btnConnect.setText(R.string.disconnect);
                btnConnect.setEnabled(true);
                tvStatus.setText(R.string.connected);
                tvStatus.setTextColor(getResources().getColor(R.color.colorSuccess));
                Toast.makeText(MainActivity.this, "连接成功", Toast.LENGTH_SHORT).show();
            }

            @Override
            public void onDisconnected() {
                isConnected = false;
                btnConnect.setText(R.string.connect);
                btnConnect.setEnabled(true);
                if (userRequestedConnect) {
                    // 断线自动重连：显示倒计时状态，5 秒后重试
                    tvStatus.setText("已断开，正在自动重连…");
                    tvStatus.setTextColor(getResources().getColor(R.color.colorWarning));
                    mainHandler.removeCallbacks(reconnectRunnable);
                    mainHandler.postDelayed(reconnectRunnable, RECONNECT_DELAY_MS);
                } else {
                    tvStatus.setText(R.string.disconnected);
                    tvStatus.setTextColor(getResources().getColor(R.color.colorError));
                }
            }

            @Override
            public void onConnectionFailed(String error) {
                isConnected = false;
                btnConnect.setText(R.string.connect);
                btnConnect.setEnabled(true);
                if (userRequestedConnect) {
                    tvStatus.setText("连接失败，正在自动重连…");
                    tvStatus.setTextColor(getResources().getColor(R.color.colorWarning));
                    mainHandler.removeCallbacks(reconnectRunnable);
                    mainHandler.postDelayed(reconnectRunnable, RECONNECT_DELAY_MS);
                } else {
                    tvStatus.setText(R.string.disconnected);
                    tvStatus.setTextColor(getResources().getColor(R.color.colorError));
                    Toast.makeText(MainActivity.this, error, Toast.LENGTH_SHORT).show();
                }
            }
        });

        tcpClient.setDataReceivedListener(new TcpClient.OnDataReceivedListener() {
            @Override
            public void onDataReceived(SensorData data) {
                lastDataTime = SystemClock.elapsedRealtime();
                updateSensorData(data);
                // P1：节流上报到 Flask /api/sensor，持续写入 sensor.db 供墒情预测训练
                SensorUploader.report(MainActivity.this, data);
            }
        });

        lastDataTime = SystemClock.elapsedRealtime();
        tcpClient.connect();
    }

    private void disconnect() {
        // 用户主动断开：关掉自动重连和看门狗
        userRequestedConnect = false;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        if (tcpClient != null) {
            tcpClient.disconnect();
            tcpClient = null;
        }
        isConnected = false;
        btnConnect.setText(R.string.connect);
        tvStatus.setText(R.string.disconnected);
        tvStatus.setTextColor(getResources().getColor(R.color.colorError));
    }

    private void updateSensorData(SensorData data) {
        tvTempValue.setText(String.valueOf(data.getTemperature()));
        tvHumiValue.setText(String.valueOf(data.getHumidity()));
        tvLightValue.setText(String.valueOf(data.getLightLux()));
        tvSoilValue.setText(String.valueOf(data.getSoilHumidity()));
        tvWaterValue.setText(String.valueOf(data.getWaterLevel()));
    }

    private void showThresholdDialog() {
        if (!isConnected) {
            Toast.makeText(this, "请先连接到设备", Toast.LENGTH_SHORT).show();
            return;
        }

        View dialogView = getLayoutInflater().inflate(R.layout.dialog_threshold, null);

        final EditText etTemp = dialogView.findViewById(R.id.et_temp_threshold);
        final EditText etHumi = dialogView.findViewById(R.id.et_humi_threshold);
        final EditText etLight = dialogView.findViewById(R.id.et_light_threshold);
        final EditText etWater = dialogView.findViewById(R.id.et_water_threshold);

        etTemp.setText(String.valueOf(tempThreshold));
        etHumi.setText(String.valueOf(humiThreshold));
        etLight.setText(String.valueOf(lightThreshold));
        etWater.setText(String.valueOf(waterThreshold));

        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        builder.setView(dialogView);

        builder.setPositiveButton(R.string.save, new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                try {
                    int temp = Integer.parseInt(etTemp.getText().toString().trim());
                    int humi = Integer.parseInt(etHumi.getText().toString().trim());
                    int light = Integer.parseInt(etLight.getText().toString().trim());
                    int water = Integer.parseInt(etWater.getText().toString().trim());

                    tempThreshold = temp;
                    humiThreshold = humi;
                    lightThreshold = light;
                    waterThreshold = water;

                    tcpClient.sendThresholdCommand(temp, humi, light, 10, water);

                    Toast.makeText(MainActivity.this, "阈值设置已发送", Toast.LENGTH_SHORT).show();
                } catch (NumberFormatException e) {
                    Toast.makeText(MainActivity.this, "请输入有效的数值", Toast.LENGTH_SHORT).show();
                }
            }
        });

        builder.setNegativeButton(R.string.cancel, new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                dialog.dismiss();
            }
        });

        builder.show();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // 退出页面：清掉所有定时任务再断开
        userRequestedConnect = false;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        disconnect();
        if (diseasePoller != null) {
            diseasePoller.stop();
            diseasePoller = null;
        }
    }
}