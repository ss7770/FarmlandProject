package com.example.smartfarm;

import android.content.DialogInterface;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AlertDialog;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;

import com.google.android.material.switchmaterial.SwitchMaterial;

/**
 * 首页（原生，2026-10-06 参考图同款重构）：
 * 绿 Header（状态胶囊）→ 设备概览（灰瓷砖）→ 设备控制 → 灌溉统计 → 环境舒适度。
 *
 * <p>数据来源：只认 {@link DeviceHost}，自己不碰 TcpClient、不建任何定时器。
 * 视图可能在 Activity 回调时还没建好/已销毁，所以 onStart 注册、onStop 注销，
 * 注册时由宿主立即回放当前值（见 MainActivity#setSensorListener）。
 *
 * <p>新增两张卡的口径：
 * <ul>
 *   <li>灌溉统计 = 水位变化<b>推断</b>，估算值（{@link IrrigationEstimator}）；</li>
 *   <li>环境舒适度 = 纯规则打分，<b>不是模型预测</b>（{@link ComfortEvaluator}）。</li>
 * </ul>
 */
public class HomeFragment extends Fragment implements DeviceHost.SensorListener {

    /** 水位"偏低"阈值（ADC），与 STM32 waterMin=1000 同源；同时作为进度条区间下界 */
    private static final int WATER_LO = 1000;
    /** 水位 ADC 上限（12 位） */
    private static final int WATER_HI = 4095;
    /** 光照适宜区间（lux）：与阈值设置里的 lightThreshold=800 对齐 */
    private static final int LIGHT_LO = 300;
    private static final int LIGHT_HI = 800;

    private View dotStatus, dotComfort;
    private TextView tvHomeStatus;
    private TextView tvTemp, tvHumi, tvLight, tvSoil, tvWater;
    private TextView tvWaterPill;
    private TextView pillTemp, pillHumi, pillLight, pillSoil;
    private StatBarView barTemp, barHumi, barLight, barSoil, barWater;
    private TextView tvIrrigationState, tvIrrigationCount, tvIrrigationVolume;
    private TextView tvComfortLevel, tvComfortDesc, pillTempBand, pillHumiBand, pillSoilBand;
    private SwitchMaterial swIrrigation;
    private ScoreRingView scoreRing;

    private IrrigationEstimator estimator;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_home, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        // Header 状态胶囊
        View pillStatus = view.findViewById(R.id.pill_status);
        dotStatus = view.findViewById(R.id.dot_status);
        tvHomeStatus = view.findViewById(R.id.tv_home_status);

        // 传感器五项（值 + 右上角状态角标 + 下方彩色进度条）
        tvTemp = view.findViewById(R.id.tv_temp_value);
        tvHumi = view.findViewById(R.id.tv_humi_value);
        tvLight = view.findViewById(R.id.tv_light_value);
        tvSoil = view.findViewById(R.id.tv_soil_value);
        tvWater = view.findViewById(R.id.tv_water_value);
        tvWaterPill = view.findViewById(R.id.tv_water_pill);
        pillTemp = view.findViewById(R.id.pill_temp);
        pillHumi = view.findViewById(R.id.pill_humi);
        pillLight = view.findViewById(R.id.pill_light);
        pillSoil = view.findViewById(R.id.pill_soil);
        barTemp = view.findViewById(R.id.bar_temp);
        barHumi = view.findViewById(R.id.bar_humi);
        barLight = view.findViewById(R.id.bar_light);
        barSoil = view.findViewById(R.id.bar_soil);
        barWater = view.findViewById(R.id.bar_water);

        // 设备控制
        tvIrrigationState = view.findViewById(R.id.tv_irrigation_state);
        swIrrigation = view.findViewById(R.id.sw_irrigation);

        // 灌溉统计（水位推断 · 估算）
        estimator = new IrrigationEstimator(requireContext());
        tvIrrigationCount = view.findViewById(R.id.tv_irrigation_count);
        tvIrrigationVolume = view.findViewById(R.id.tv_irrigation_volume);
        updateIrrigationStats();

        // 环境舒适度（纯规则打分）
        scoreRing = view.findViewById(R.id.score_ring);
        dotComfort = view.findViewById(R.id.dot_comfort);
        tvComfortLevel = view.findViewById(R.id.tv_comfort_level);
        tvComfortDesc = view.findViewById(R.id.tv_comfort_desc);
        pillTempBand = view.findViewById(R.id.pill_temp_band);
        pillHumiBand = view.findViewById(R.id.pill_humi_band);
        pillSoilBand = view.findViewById(R.id.pill_soil_band);

        // 状态胶囊 → 连接配置弹窗（原状态条白卡的职责移到这里）
        pillStatus.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showConnectDialog();
            }
        });

        // 阈值设置 → 复用 Activity 里原有的对话框（TCP 命令通道不变）
        view.findViewById(R.id.card_threshold).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                host().openThresholdDialog();
            }
        });

        // 自动灌溉：**刻意不接任何网络逻辑**，只改显示（用户 2026-10-05 明确要求"不必实际作用"）
        swIrrigation.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                tvIrrigationState.setText(isChecked
                        ? R.string.irrigation_state_on
                        : R.string.irrigation_state_off);
            }
        });
        tvIrrigationState.setText(swIrrigation.isChecked()
                ? R.string.irrigation_state_on
                : R.string.irrigation_state_off);
    }

    @Override
    public void onStart() {
        super.onStart();
        // 视图已存在 → 注册并立即回放当前值
        host().setSensorListener(this);
    }

    @Override
    public void onStop() {
        // 视图即将销毁 → 注销，避免宿主往已销毁的视图写数据
        host().setSensorListener(null);
        super.onStop();
    }

    private DeviceHost host() {
        return (DeviceHost) requireActivity();
    }

    // ==================== DeviceHost.SensorListener ====================

    @Override
    public void onSensorData(SensorData data) {
        if (tvTemp == null) return;

        tvTemp.setText(String.valueOf(data.getTemperature()));
        tvHumi.setText(String.valueOf(data.getHumidity()));
        tvLight.setText(String.valueOf(data.getLightLux()));
        tvSoil.setText(String.valueOf(data.getSoilHumidity()));
        tvWater.setText(String.valueOf(data.getWaterLevel()));

        // 五卡角标 + 彩色进度条（区间口径：温/湿/土 = ComfortEvaluator 同源常量；
        // 光照 = 与阈值设置的 lightThreshold=800 对齐；水位 = STM32 waterMin=1000 ~ 12 位上限 4095）
        bindStat(barTemp, pillTemp, data.getTemperature(),
                ComfortEvaluator.TEMP_LO, ComfortEvaluator.TEMP_HI,
                R.color.colorBadgeTempStart, R.color.colorBadgeTempEnd);
        bindStat(barHumi, pillHumi, data.getHumidity(),
                ComfortEvaluator.HUMI_LO, ComfortEvaluator.HUMI_HI,
                R.color.colorBadgeHumiStart, R.color.colorBadgeHumiEnd);
        bindStat(barLight, pillLight, data.getLightLux(),
                LIGHT_LO, LIGHT_HI,
                R.color.colorBadgeLightStart, R.color.colorBadgeLightEnd);
        bindStat(barSoil, pillSoil, data.getSoilHumidity(),
                ComfortEvaluator.SOIL_LO, ComfortEvaluator.SOIL_HI,
                R.color.colorBadgeSoilStart, R.color.colorBadgeSoilEnd);
        bindStat(barWater, tvWaterPill, data.getWaterLevel(),
                WATER_LO, WATER_HI,
                R.color.colorBadgeWaterStart, R.color.colorBadgeWaterEnd);

        // 灌溉统计：水位推断（1 秒一帧喂进来）
        estimator.feed(data.getWaterLevel(), System.currentTimeMillis());
        updateIrrigationStats();

        // 环境舒适度：纯规则打分
        updateComfort(ComfortEvaluator.evaluate(
                data.getTemperature(), data.getHumidity(), data.getSoilHumidity()));
    }

    @Override
    public void onConnectionState(boolean connected, String text, int colorRes) {
        if (tvHomeStatus == null) return;
        // 胶囊文字恒为白色（深绿底），只让小圆点变色表达状态
        tvHomeStatus.setText(text);
        int dotColor = ContextCompat.getColor(requireContext(),
                connected ? R.color.colorSuccess : R.color.colorNavUnselected);
        dotStatus.setBackgroundTintList(ColorStateList.valueOf(dotColor));
    }

    // ==================== 灌溉统计 / 舒适度渲染 ====================

    /**
     * 传感器卡统一渲染：右上角三态角标 + 下方彩色渐变进度条。
     *
     * <p>区间口径 = 工程真实阈值（不照抄参考图的装饰性数字）：
     * <ul>
     *   <li>value &lt; lo → 角标「偏低」（橙），进度条放空；</li>
     *   <li>value &gt; hi → 角标「偏高」（橙），进度条填满；</li>
     *   <li>区间内 → 角标「正常」（绿），进度条 = (value-lo)/(hi-lo)。</li>
     * </ul>
     */
    private void bindStat(StatBarView bar, TextView pill, double value,
                          double lo, double hi, int startColorRes, int endColorRes) {
        boolean ok = value >= lo && value <= hi;
        boolean low = value < lo;
        pill.setText(low ? R.string.status_low : (ok ? R.string.status_normal : R.string.status_high));
        pill.setBackgroundResource(ok ? R.drawable.bg_pill_green : R.drawable.bg_pill_orange);
        pill.setTextColor(ContextCompat.getColor(requireContext(),
                ok ? R.color.colorPillGreenText : R.color.colorPillOrangeText));
        // 越界由 StatBarView 内部裁剪：低于下限 → 0（放空），高于上限 → 1（填满）
        bar.setProgress((float) ((value - lo) / (hi - lo)), startColorRes, endColorRes);
    }

    private void updateIrrigationStats() {
        tvIrrigationCount.setText(getString(R.string.irrigation_count_value, estimator.getCount()));
        tvIrrigationVolume.setText(getString(R.string.irrigation_volume_value, estimator.getVolume()));
    }

    private void updateComfort(ComfortEvaluator.Result r) {
        scoreRing.setScore(r.score, comfortColor(r.level));

        int levelText, levelColor, descText;
        switch (r.level) {
            case ComfortEvaluator.LEVEL_GOOD:
                levelText = R.string.comfort_level_good;
                levelColor = R.color.colorSuccess;
                descText = R.string.comfort_desc_good;
                break;
            case ComfortEvaluator.LEVEL_MID:
                levelText = R.string.comfort_level_mid;
                levelColor = R.color.colorPillOrangeText;
                descText = R.string.comfort_desc_mid;
                break;
            default:
                levelText = R.string.comfort_level_bad;
                levelColor = R.color.colorError;
                descText = R.string.comfort_desc_bad;
                break;
        }
        tvComfortLevel.setText(levelText);
        tvComfortLevel.setTextColor(ContextCompat.getColor(requireContext(), levelColor));
        dotComfort.setBackgroundTintList(
                ColorStateList.valueOf(ContextCompat.getColor(requireContext(), levelColor)));
        tvComfortDesc.setText(descText);

        applyBandPill(pillTempBand, R.string.temperature, r.tempBand);
        applyBandPill(pillHumiBand, R.string.humidity, r.humiBand);
        applyBandPill(pillSoilBand, R.string.soil_moisture, r.soilBand);
    }

    /** 胶囊统一渲染：文字 =「标签 + 档位词」，底色/文字色随档位 */
    private void applyBandPill(TextView pill, int labelRes, int band) {
        int bandText = band == ComfortEvaluator.BAND_OK
                ? R.string.comfort_band_ok
                : (band == ComfortEvaluator.BAND_HIGH
                        ? R.string.comfort_band_high : R.string.comfort_band_low);
        pill.setText(getString(labelRes) + " " + getString(bandText));
        boolean ok = band == ComfortEvaluator.BAND_OK;
        applyPill(pill, 0,
                ok ? R.drawable.bg_pill_green : R.drawable.bg_pill_orange,
                ok ? R.color.colorPillGreenText : R.color.colorPillOrangeText);
    }

    /** textRes 传 0 表示不改文字（只换底色/文字色） */
    private void applyPill(TextView pill, int textRes, int bgRes, int textColorRes) {
        if (textRes != 0) pill.setText(textRes);
        pill.setBackgroundResource(bgRes);
        pill.setTextColor(ContextCompat.getColor(requireContext(), textColorRes));
    }

    private int comfortColor(int level) {
        int res = level == ComfortEvaluator.LEVEL_GOOD ? R.color.colorSuccess
                : (level == ComfortEvaluator.LEVEL_MID ? R.color.colorPillOrangeText
                        : R.color.colorError);
        return ContextCompat.getColor(requireContext(), res);
    }

    // ==================== 连接弹窗 ====================

    private void showConnectDialog() {
        final DeviceHost host = host();
        View dialogView = LayoutInflater.from(requireContext())
                .inflate(R.layout.dialog_connect, null);
        final EditText etIp = dialogView.findViewById(R.id.et_ip);
        final EditText etPort = dialogView.findViewById(R.id.et_port);

        etIp.setText(host.getDeviceIp());
        etPort.setText(String.valueOf(host.getDevicePort()));

        final boolean connected = host.isConnected();

        new AlertDialog.Builder(requireContext())
                .setTitle(R.string.connect_dialog_title)
                .setView(dialogView)
                .setPositiveButton(connected ? R.string.disconnect : R.string.connect,
                        new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface dialog, int which) {
                                if (connected) {
                                    host.disconnectDevice();
                                    return;
                                }
                                String ip = etIp.getText().toString().trim();
                                String portStr = etPort.getText().toString().trim();
                                if (TextUtils.isEmpty(ip)) {
                                    Toast.makeText(requireContext(), R.string.enter_ip,
                                            Toast.LENGTH_SHORT).show();
                                    return;
                                }
                                if (TextUtils.isEmpty(portStr)) {
                                    Toast.makeText(requireContext(), R.string.enter_port,
                                            Toast.LENGTH_SHORT).show();
                                    return;
                                }
                                int port;
                                try {
                                    port = Integer.parseInt(portStr);
                                } catch (NumberFormatException e) {
                                    Toast.makeText(requireContext(), R.string.enter_port,
                                            Toast.LENGTH_SHORT).show();
                                    return;
                                }
                                host.connectDevice(ip, port);
                            }
                        })
                .setNegativeButton(R.string.cancel, null)
                .show();
    }
}
