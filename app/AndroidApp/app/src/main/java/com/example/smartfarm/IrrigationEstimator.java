package com.example.smartfarm;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.Map;

/**
 * 灌溉统计（2026-10-06 首页新增）：**由水位变化推断灌溉事件**，结果为估算值。
 *
 * <p>原理与前提：水位传感器装在<b>水泵取水的水箱</b>上 —— 泵抽水时水位下降。
 * 水位较慢基线显著且持续下降 ⇒ 判定一次灌溉；水位下降量 × 换算系数 ⇒ 估算水量；
 * 水位上升（加水/回补）只抬基线、不计数。
 *
 * <p>为什么用「慢基线」而不是帧间差分：水位 ADC 有抖动、且传感器读数漂移，
 * 帧间差分会被噪声打成碎片；EMA 慢基线（约 60s 收敛）只对"持续十几秒以上的下降"敏感。
 *
 * <p>⚠️ 两个现场标定常量：{@link #DROP_UNITS}（判定阈值，ADC 单位）与
 * {@link #LITERS_PER_UNIT}（水位单位 → 升的换算系数），默认值是拍脑袋的工程估计，
 * 上板后用「量筒实测一次抽水」校准。
 *
 * <p>纯逻辑类（仅依赖 Context 取 SharedPreferences）；按天持久化，切 Tab / 重启不丢，
 * 跨天自动清零并清理旧 key。
 */
public class IrrigationEstimator {

    /** 判定"显著下降"的幅度（ADC 单位）。现场标定。 */
    public static final int DROP_UNITS = 15;
    /** 水位 1 个 ADC 单位 ≈ 多少升水。现场标定（默认按小水箱毛估）。 */
    public static final double LITERS_PER_UNIT = 0.05;
    /** 连续多少帧低于基线才判定"开始灌溉"（STM32 1 秒一帧，即 ~3 秒）。 */
    static final int DROP_FRAMES = 3;
    /** 进入灌溉中之后，持续这么久不再下降 → 判定结束。 */
    static final long IDLE_END_MS = 10_000L;
    /** 慢基线 EMA 系数（每帧 2%，约 60 秒收敛）。 */
    static final double EMA_ALPHA = 0.02;
    /** 帧间抖动容差：小于它的变化视为噪声。 */
    static final double JITTER = 1.5;
    /** 断线超过这么久，基线不可信，重建。 */
    static final long GAP_RESET_MS = 120_000L;

    private static final String PREFS_NAME = "irrigation_stats";
    private static final SimpleDateFormat DAY_FMT = new SimpleDateFormat("yyyyMMdd", Locale.US);

    private final SharedPreferences prefs;
    private String today = "";
    private int count = 0;
    private double volume = 0; // 升

    // 运行态（不持久化：重启后基线重建，代价只是丢一次跨重启的进行中事件）
    private double baseline = -1;
    private double lastLevel = -1;
    private long lastMs = 0;
    private int dropRun = 0;
    private boolean tracking = false;
    private double startLevel = 0;
    private long stableSinceMs = 0;

    public IrrigationEstimator(Context context) {
        prefs = context.getApplicationContext()
                .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        rollDayIfNeeded(System.currentTimeMillis());
    }

    /** 每帧喂一次（STM32 1 秒一帧）。内部自动跨天清零。 */
    public void feed(int level, long nowMs) {
        rollDayIfNeeded(nowMs);

        if (lastLevel < 0) { // 第一帧：只建基线
            lastLevel = level;
            baseline = level;
            lastMs = nowMs;
            return;
        }

        long gap = nowMs - lastMs;
        lastMs = nowMs;
        double prev = lastLevel;
        lastLevel = level;

        if (gap > GAP_RESET_MS) { // 断线重连：基线不可信
            baseline = level;
            dropRun = 0;
            tracking = false;
            return;
        }

        if (level > prev + JITTER) {
            // 上升（加水/回补）：只抬基线，不计数；进行中的事件就地结算
            if (level > baseline) baseline = level;
            if (tracking) endEvent();
            dropRun = 0;
            stableSinceMs = nowMs;
        } else if (level < prev - JITTER && level < baseline - DROP_UNITS) {
            // 显著下降：连续 DROP_FRAMES 帧才判定开始，压单帧噪声
            if (!tracking) {
                startLevel = prev;      // 下降前一刻的水位
                dropRun++;
                if (dropRun >= DROP_FRAMES) {
                    tracking = true;
                    stableSinceMs = nowMs;
                }
            } else {
                stableSinceMs = nowMs;  // 还在降
            }
        } else {
            dropRun = 0;
            if (tracking && nowMs - stableSinceMs >= IDLE_END_MS) {
                endEvent();             // 连续 10s 不再下降 → 结束
            }
            // 平稳：慢基线向当前值收敛
            baseline += EMA_ALPHA * (level - baseline);
        }
    }

    public int getCount() { return count; }

    public double getVolume() { return volume; }

    private void endEvent() {
        tracking = false;
        double drop = startLevel - lastLevel;
        if (drop > 0) {
            count++;
            volume += drop * LITERS_PER_UNIT;
            save();
        }
    }

    /** 跨天清零：读当天的计数，并清掉旧 key。 */
    private void rollDayIfNeeded(long nowMs) {
        String day = DAY_FMT.format(new Date(nowMs));
        if (day.equals(today)) return;
        today = day;
        count = prefs.getInt(today + "_count", 0);
        volume = prefs.getFloat(today + "_volume", 0f);
        // 清理历史日期的 key（只保留今天）
        Map<String, ?> all = prefs.getAll();
        SharedPreferences.Editor ed = prefs.edit();
        boolean dirty = false;
        for (String key : all.keySet()) {
            if (!key.startsWith(today)) {
                ed.remove(key);
                dirty = true;
            }
        }
        if (dirty) ed.apply();
        save();
    }

    private void save() {
        prefs.edit()
                .putInt(today + "_count", count)
                .putFloat(today + "_volume", (float) volume)
                .apply();
    }
}
