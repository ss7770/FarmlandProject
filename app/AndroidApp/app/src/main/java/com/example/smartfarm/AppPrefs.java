package com.example.smartfarm;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * App 本地偏好（2026-10-06）：深色模式 + AI 助手语音 + 连接意图。
 *
 * <p>复用已有的 {@code app_config} 文件（ServerConfig / DevicePrefs 也在里面），
 * 只新增 key，互不干扰。
 *
 * <p>默认值口径：
 * <ul>
 *   <li>dark_mode = false（默认浅色，只在用户手动打开后才变暗，不跟随系统）</li>
 *   <li>tts_enabled = true（AI 助手语音默认开）</li>
 *   <li>was_connected = false（上次是否处于"用户想连着"的状态）</li>
 * </ul>
 */
final class AppPrefs {

    private static final String PREFS = "app_config";
    private static final String KEY_DARK = "dark_mode";
    private static final String KEY_TTS = "tts_enabled";
    private static final String KEY_WAS_CONNECTED = "was_connected";

    private AppPrefs() {
    }

    private static SharedPreferences sp(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean isDarkMode(Context ctx) {
        return sp(ctx).getBoolean(KEY_DARK, false);
    }

    static void setDarkMode(Context ctx, boolean on) {
        sp(ctx).edit().putBoolean(KEY_DARK, on).apply();
    }

    static boolean isTtsEnabled(Context ctx) {
        return sp(ctx).getBoolean(KEY_TTS, true);
    }

    static void setTtsEnabled(Context ctx, boolean on) {
        sp(ctx).edit().putBoolean(KEY_TTS, on).apply();
    }

    /**
     * 上次是否"连着设备/想连着"。切换深色模式会重建 Activity（TCP 必断），
     * 重建后靠它判断要不要自动重连——不然每次切主题都要手动连一次。
     * 用户主动断开时置 false。
     */
    static boolean wasConnected(Context ctx) {
        return sp(ctx).getBoolean(KEY_WAS_CONNECTED, false);
    }

    static void setWasConnected(Context ctx, boolean on) {
        sp(ctx).edit().putBoolean(KEY_WAS_CONNECTED, on).apply();
    }
}
