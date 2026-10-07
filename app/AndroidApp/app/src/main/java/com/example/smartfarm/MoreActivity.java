package com.example.smartfarm;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 网页看板容器：用 WebView 打开 Flask 端的某个页面。
 *
 * 2026-10-01 起，APP 不再自带原生病害识别页（已删除），
 * 「病害识别看板」按钮与病害巡检通知都改为打开 Web 端的 /disease 页——
 * 手机端与电脑端因此共用同一套看板，口径、样式、功能只有一份。
 *
 * - 「更多」按钮   → 首页 dashboard（传感器图表 + 天气灌溉建议 + AI 决策）
 * - 「病害识别」按钮 → /disease（设备状态 + 最新结果 + 上传识别 + 记录列表）
 *
 * 目标页由 {@link #EXTRA_PATH} 指定，构造 Intent 请用 {@link #intentFor}。
 *
 * 注意：/disease 页里的「上传图片识别」是一个原生 {@code <input type="file">}，
 * WebView 默认不处理它（点了没反应）。所以这里必须实现
 * {@link WebChromeClient#onShowFileChooser} 转发系统文件选择器，
 * 否则手机上"上传识别"这条路是断的。
 */
public class MoreActivity extends AppCompatActivity {

    /** Intent extra：要加载的页面路径（相对后端根，如 "/" 或 "/disease"） */
    public static final String EXTRA_PATH = "extra_path";
    /** 首页看板 */
    public static final String PATH_HOME = "/";
    /** 病害识别看板 */
    public static final String PATH_DISEASE = "/disease";

    /** 打开系统文件选择器的请求码 */
    private static final int REQ_FILE_CHOOSER = 1001;

    private WebView webMore;
    /** 等待文件选择结果的回调（网页里 <input type="file"> 的 onchange 依赖它） */
    private ValueCallback<Uri[]> filePathCallback;

    /** 构造一个「打开指定看板页」的 Intent，避免各处散落 putExtra */
    public static Intent intentFor(Context context, String path) {
        Intent intent = new Intent(context, MoreActivity.class);
        intent.putExtra(EXTRA_PATH, path);
        return intent;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_more);

        String path = getIntent().getStringExtra(EXTRA_PATH);
        if (path == null || path.length() == 0) {
            path = PATH_HOME;
        } else if (!path.startsWith("/")) {
            path = "/" + path;
        }

        webMore = findViewById(R.id.web_more);
        WebSettings settings = webMore.getSettings();
        settings.setJavaScriptEnabled(true);          // 页面里的 ECharts / 看板轮询需要 JS
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setAllowFileAccess(true);            // 让文件选择器取到的 Uri 能被读取
        // 让 window.open(url)（点现场图放大时用）在当前 WebView 内打开，而不是被静默丢弃
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);

        // 页内链接（如「返回看板」）留在本 WebView 里打开，不弹外部浏览器
        webMore.setWebViewClient(new WebViewClient());

        // 关键：接管网页里的文件选择（/disease 的「上传图片识别」）
        webMore.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                // 上一次未消费的回调要先释放，否则第二次点击会失效
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), REQ_FILE_CHOOSER);
                } catch (Exception e) {
                    filePathCallback = null;
                    return false;   // 没有可用的文件选择器
                }
                return true;
            }
        });

        webMore.loadUrl(ServerConfig.get(this) + path);
    }

    /** 把系统文件选择器的结果交回网页 */
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER) {
            if (filePathCallback != null) {
                Uri[] results = null;
                if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
                filePathCallback.onReceiveValue(results);
                filePathCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
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
