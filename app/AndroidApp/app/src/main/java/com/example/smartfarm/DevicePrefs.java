package com.example.smartfarm;

import android.content.Context;

/**
 * 现场设备（ESP8266 网关，TCP 8288）的地址记忆（2026-10-05）。
 *
 * <p>原来这两个值只是 MainActivity 里的内存变量 + 布局里写死的默认值，
 * 换到 Fragment 之后需要一个"谁都能读"的地方——顺便让「设备管理」页能显示真实地址。
 * 默认值与旧布局保持一致（192.168.57.182:8288）。
 */
final class DevicePrefs {

    private static final String PREFS = "app_config";
    private static final String KEY_IP = "device_ip";
    private static final String KEY_PORT = "device_port";

    static final String DEFAULT_IP = "192.168.57.182";
    static final int DEFAULT_PORT = 8288;

    private DevicePrefs() {
    }

    static String getIp(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_IP, DEFAULT_IP);
    }

    static int getPort(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getInt(KEY_PORT, DEFAULT_PORT);
    }

    static void set(Context ctx, String ip, int port) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_IP, ip)
                .putInt(KEY_PORT, port)
                .apply();
    }
}
