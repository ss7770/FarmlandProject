package com.example.smartfarm;

import android.os.Bundle;
import android.view.MenuItem;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 设备管理子页（2026-10-05）。
 *
 * <p>目前只有一台设备（ESP8266 网关），所以这里是"一条静态条目 + 说明文字"。
 * 布局与条目布局（item_device.xml + device_list 容器）已经按多台设计，
 * 将来接入多台时只需把 addView 换成列表适配器即可，不用重排界面。
 */
public class DeviceManageActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_device_manage);

        // 2026-10-06 全局去 ActionBar：返回改走自绘标题行的箭头
        findViewById(R.id.btn_back).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });

        TextView tvCount = findViewById(R.id.tv_device_count);
        tvCount.setText(R.string.device_count_one);

        LinearLayout list = findViewById(R.id.device_list);
        View item = getLayoutInflater().inflate(R.layout.item_device, list, false);

        TextView tvName = item.findViewById(R.id.tv_device_name);
        TextView tvAddr = item.findViewById(R.id.tv_device_addr);
        tvName.setText(R.string.device_default_name);
        tvAddr.setText(DevicePrefs.getIp(this) + ":" + DevicePrefs.getPort(this));

        list.addView(item);
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == android.R.id.home) {
            finish();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }
}
