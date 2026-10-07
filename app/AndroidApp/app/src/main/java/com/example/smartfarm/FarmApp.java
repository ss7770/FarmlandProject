package com.example.smartfarm;

import android.app.Application;

import androidx.appcompat.app.AppCompatDelegate;

/**
 * 应用入口（2026-10-06）：冷启动第一件事就把暗色模式按用户偏好设好。
 *
 * <p>为什么需要它：只在设置页调 setDefaultNightMode 的话，杀进程重开后
 * Activity 会先用系统默认（浅色）创建一次再被偏好覆盖，视觉上会"闪一下浅色"。
 * 放在 Application.onCreate 里可以让首次创建就用对的模式。
 *
 * <p>口径：只区分 YES/NO，不跟随系统（不用 MODE_NIGHT_FOLLOW_SYSTEM）；默认浅色。
 */
public class FarmApp extends Application {

    @Override
    public void onCreate() {
        super.onCreate();
        AppCompatDelegate.setDefaultNightMode(
                AppPrefs.isDarkMode(this)
                        ? AppCompatDelegate.MODE_NIGHT_YES
                        : AppCompatDelegate.MODE_NIGHT_NO);
    }
}
