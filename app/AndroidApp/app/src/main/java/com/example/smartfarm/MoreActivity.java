package com.example.smartfarm;

import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.appcompat.app.AppCompatActivity;

/**
 * “更多”看板页：WebView 加载 Flask 端 dashboard（标题“更多”），
 * 内容与 Web 端完全一致：传感器图表 + 今日天气&灌溉建议 + 病害巡检记录，
 * 页内导航可跳转历史数据看板。
 */
public class MoreActivity extends AppCompatActivity {

    private WebView webMore;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_more);

        webMore = findViewById(R.id.web_more);
        WebSettings settings = webMore.getSettings();
        settings.setJavaScriptEnabled(true);          // 页面里的 ECharts 需要 JS
        settings.setDomStorageEnabled(true);          // ECharts 本地存储
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);

        // 链接（如“历史数据”）留在本页打开，不弹外部浏览器
        webMore.setWebViewClient(new WebViewClient());
        webMore.loadUrl(DiseasePoller.SERVER_BASE + "/");
    }

    /** WebView 有历史记录时，返回键先回退网页而不是退出页面 */
    @Override
    public void onBackPressed() {
        if (webMore != null && webMore.canGoBack()) {
            webMore.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
