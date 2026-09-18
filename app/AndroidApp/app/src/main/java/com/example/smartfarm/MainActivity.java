package com.example.smartfarm;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import android.content.DialogInterface;
import android.content.Intent;
import android.os.Bundle;
import android.text.TextUtils;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends AppCompatActivity {
    private static final String TAG = "MainActivity";

    private EditText etIp, etPort;
    private Button btnConnect, btnThreshold, btnDisease, btnMore;
    private TextView tvStatus, tvTempValue, tvHumiValue, tvLightValue, tvSoilValue, tvWaterValue;

    private TcpClient tcpClient;
    private boolean isConnected = false;

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

        tvStatus.setText(R.string.connecting);
        tvStatus.setTextColor(getResources().getColor(R.color.colorWarning));
        btnConnect.setEnabled(false);

        tcpClient = new TcpClient(ip, port);
        tcpClient.setConnectionListener(new TcpClient.OnConnectionListener() {
            @Override
            public void onConnected() {
                isConnected = true;
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
                tvStatus.setText(R.string.disconnected);
                tvStatus.setTextColor(getResources().getColor(R.color.colorError));
            }

            @Override
            public void onConnectionFailed(String error) {
                isConnected = false;
                btnConnect.setText(R.string.connect);
                btnConnect.setEnabled(true);
                tvStatus.setText(R.string.disconnected);
                tvStatus.setTextColor(getResources().getColor(R.color.colorError));
                Toast.makeText(MainActivity.this, error, Toast.LENGTH_SHORT).show();
            }
        });

        tcpClient.setDataReceivedListener(new TcpClient.OnDataReceivedListener() {
            @Override
            public void onDataReceived(SensorData data) {
                updateSensorData(data);
            }
        });

        tcpClient.connect();
    }

    private void disconnect() {
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
        disconnect();
        if (diseasePoller != null) {
            diseasePoller.stop();
            diseasePoller = null;
        }
    }
}