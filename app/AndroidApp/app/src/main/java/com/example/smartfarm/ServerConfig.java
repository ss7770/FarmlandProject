package com.example.smartfarm;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * Flask 服务器地址配置（P0：消除硬编码 IP）。
 * 地址存于 SharedPreferences，换网络环境后在 APP 内"服务器地址设置"里改即可，
 * 不再需要改代码重新编译。默认值仅作初始兜底。
 */
public final class ServerConfig {

    private static final String PREFS = "app_config";
    private static final String KEY_SERVER_BASE = "server_base";

    /** 默认地址：仅首次启动且未设置过时使用（包内可见，DiseasePoller 兼容引用） */
    static final String DEFAULT_SERVER_BASE = "http://192.168.57.97:5000";

    private ServerConfig() {}//工具类，禁止实例化

    /** 读取当前服务器地址（形如 http://192.168.x.x:5000，末尾不带斜杠） */
    public static String get(Context context) {
        Context app = context.getApplicationContext();
        SharedPreferences prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return prefs.getString(KEY_SERVER_BASE, DEFAULT_SERVER_BASE);
    }

    /** 保存服务器地址（自动去掉末尾斜杠；空串视为放弃修改） */
    public static void set(Context context, String baseUrl) {
        if (baseUrl == null) return;
        baseUrl = baseUrl.trim();
        if (baseUrl.length() == 0) return;
        while (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        }
        Context app = context.getApplicationContext();
        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_SERVER_BASE, baseUrl).apply();
    }
}
