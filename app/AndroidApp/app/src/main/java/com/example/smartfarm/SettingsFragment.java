package com.example.smartfarm;

import android.content.Intent;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatDelegate;
import androidx.fragment.app.Fragment;

import com.google.android.material.switchmaterial.SwitchMaterial;
import com.google.android.material.textfield.TextInputEditText;

/**
 * 设置（原生）：服务器地址 + 设备管理入口 + 外观（深色模式）+ 语音（AI 助手播报）+ 关于。
 *
 * <p>2026-10-06 与网页设置页融合（"都原生"）：样式吸收网页的"分组卡 + 图标徽章 + 开关"，
 * 网页 templates/settings.html 与 /settings 路由已删除，底部网页 tab-bar 也只剩 3 个入口。
 *
 * <p>服务器地址原来是首页的一个弹窗，2026-10-05 改底部 Tab 时挪到这里，改成页内输入。
 * 保存仍走 {@link ServerConfig}（SharedPreferences），WebView / 上传 / 轮询都读同一份，改完立即生效。
 *
 * <p>深色模式：写 {@link AppPrefs} 后调 {@link AppCompatDelegate#setDefaultNightMode}，
 * AppCompat 会自动重建 Activity 应用新主题（冷启动由 {@link FarmApp} 兜底，避免闪浅色）。
 * AI 助手语音：只写 AppPrefs，播报时由 {@link TtsBridge} 实时读，不需要通知网页。
 */
public class SettingsFragment extends Fragment {

    private TextInputEditText etServer;
    private TextView tvServerCurrent, tvDeviceSummary, tvAboutVersion;
    private SwitchMaterial swDarkMode, swTts;
    /** 初始化时 setChecked 会触发监听器，用它跳过回写 */
    private boolean initializing;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_settings, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        etServer = view.findViewById(R.id.et_server);
        tvServerCurrent = view.findViewById(R.id.tv_server_current);
        tvDeviceSummary = view.findViewById(R.id.tv_device_summary);
        tvAboutVersion = view.findViewById(R.id.tv_about_version);
        swDarkMode = view.findViewById(R.id.sw_dark_mode);
        swTts = view.findViewById(R.id.sw_tts);

        etServer.setText(ServerConfig.get(requireContext()));
        refreshServerText();
        tvAboutVersion.setText(getString(R.string.about_version, BuildConfig.VERSION_NAME));

        Button btnSave = view.findViewById(R.id.btn_save_server);
        btnSave.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String url = etServer.getText() == null ? "" : etServer.getText().toString();
                ServerConfig.set(requireContext(), url);
                // ServerConfig.set 会忽略空串并去掉末尾斜杠，这里回读一次显示真实值
                etServer.setText(ServerConfig.get(requireContext()));
                refreshServerText();
                Toast.makeText(requireContext(), R.string.server_settings_saved,
                        Toast.LENGTH_SHORT).show();
            }
        });

        view.findViewById(R.id.card_device_manage).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(requireContext(), DeviceManageActivity.class));
            }
        });

        bindSwitches();
    }

    /** 两个开关：初始化按 prefs 回填，之后监听写回 */
    private void bindSwitches() {
        initializing = true;
        swDarkMode.setChecked(AppPrefs.isDarkMode(requireContext()));
        swTts.setChecked(AppPrefs.isTtsEnabled(requireContext()));
        initializing = false;

        swDarkMode.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                if (initializing) return;
                AppPrefs.setDarkMode(requireContext(), isChecked);
                Toast.makeText(requireContext(),
                        isChecked ? R.string.settings_dark_mode_on : R.string.settings_dark_mode_off,
                        Toast.LENGTH_SHORT).show();
                // AppCompat 会自行重建 Activity 让新主题生效（重建后停在设置 Tab，见 MainActivity）
                AppCompatDelegate.setDefaultNightMode(isChecked
                        ? AppCompatDelegate.MODE_NIGHT_YES
                        : AppCompatDelegate.MODE_NIGHT_NO);
            }
        });

        swTts.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                if (initializing) return;
                AppPrefs.setTtsEnabled(requireContext(), isChecked);
                Toast.makeText(requireContext(),
                        isChecked ? R.string.settings_tts_on : R.string.settings_tts_off,
                        Toast.LENGTH_SHORT).show();
            }
        });
    }

    @Override
    public void onResume() {
        super.onResume();
        // 从设备管理页回来时地址可能变了，刷新一下摘要
        refreshDeviceSummary();
    }

    private void refreshServerText() {
        tvServerCurrent.setText(getString(R.string.settings_server_current,
                ServerConfig.get(requireContext())));
    }

    private void refreshDeviceSummary() {
        if (tvDeviceSummary == null) return;
        tvDeviceSummary.setText(getString(R.string.device_default_name)
                + " · " + DevicePrefs.getIp(requireContext())
                + ":" + DevicePrefs.getPort(requireContext()));
    }
}
