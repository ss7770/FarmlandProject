package com.example.smartfarm;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResult;
import androidx.activity.result.ActivityResultCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

/**
 * 网页 Tab（数据 / 病害）：直接把 Flask 页面嵌进来。
 *
 * <p>「数据」= {@code ServerConfig + "/"}（图表 + 天气 + AI 决策）
 * 「病害」= {@code ServerConfig + "/disease"}
 *
 * <p>三个关键点：
 * <ol>
 *   <li><b>懒加载</b>：首次真正可见时才 loadUrl。这样 WebView 已经有非零宽高，
 *       ECharts 才能取到正确的画布尺寸（否则会画不出来）。切 Tab 用 hide/show，
 *       实例不销毁，所以切走再切回不会白屏、也不会重新拉数据。</li>
 *   <li><b>文件选择</b>：病害页有「上传图片识别」(&lt;input type="file"&gt;)，
 *       由 onShowFileChooser + registerForActivityResult 转发到系统选择器
 *       （沿用原 MoreActivity 的做法，只是从 Activity 换到 Fragment）。</li>
 *   <li><b>返回键</b>：先退网页，退到底再交回系统。用 OnBackPressedDispatcher，
 *       且只有"当前可见"的那个网页 Tab 才拦（隐藏的 Tab 不许吞返回键）。</li>
 * </ol>
 */
public class WebTabFragment extends Fragment {

    public static final String PATH_DATA = "/";
    public static final String PATH_DISEASE = "/disease";

    private static final String ARG_PATH = "arg_path";

    private String path = PATH_DATA;

    private WebView webView;
    private ProgressBar progress;
    private View errorView;

    /** 网页 → 原生 TTS 桥（AI 助手回复播报，2026-10-06） */
    private TtsBridge ttsBridge;

    /** 网页 → 原生语音输入桥（小慧对话框麦克风，2026-10-07） */
    private VoiceBridge voiceBridge;

    /** 是否已经加载过（保证每个 Tab 只真正加载一次） */
    private boolean loaded = false;
    /** 已加载时用的服务器地址：地址改了要重新加载 */
    private String loadedBase;
    /** 网页回退用的返回键回调（切 Tab 时重新启用） */
    private OnBackPressedCallback backCallback;

    private ValueCallback<Uri[]> fileCallback;
    private ActivityResultLauncher<Intent> fileLauncher;
    /** 麦克风权限申请（语音输入首次使用时弹） */
    private ActivityResultLauncher<String> recordAudioLauncher;

    public static WebTabFragment newInstance(String path) {
        WebTabFragment f = new WebTabFragment();
        Bundle args = new Bundle();
        args.putString(ARG_PATH, path);
        f.setArguments(args);
        return f;
    }

    @Override
    public void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getArguments() != null) {
            path = getArguments().getString(ARG_PATH, PATH_DATA);
        }
        // 必须在 STARTED 之前注册，所以放在 onCreate
        fileLauncher = registerForActivityResult(
                new ActivityResultContracts.StartActivityForResult(),
                new ActivityResultCallback<ActivityResult>() {
                    @Override
                    public void onActivityResult(ActivityResult result) {
                        Uri[] uris = null;
                        Intent data = result.getData();
                        if (result.getResultCode() == Activity.RESULT_OK && data != null) {
                            if (data.getData() != null) {
                                uris = new Uri[]{data.getData()};
                            } else if (data.getClipData() != null
                                    && data.getClipData().getItemCount() > 0) {
                                uris = new Uri[]{data.getClipData().getItemAt(0).getUri()};
                            }
                        }
                        if (fileCallback != null) {
                            fileCallback.onReceiveValue(uris);
                            fileCallback = null;
                        }
                    }
                });

        // 麦克风权限（语音输入）：授权/拒绝都回给桥，由它决定是开听还是提示
        recordAudioLauncher = registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                new ActivityResultCallback<Boolean>() {
                    @Override
                    public void onActivityResult(Boolean granted) {
                        if (voiceBridge != null) {
                            voiceBridge.onPermissionResult(granted != null && granted.booleanValue());
                        }
                    }
                });
    }

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_web, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        webView = view.findViewById(R.id.web_view);
        progress = view.findViewById(R.id.web_progress);
        errorView = view.findViewById(R.id.web_error);

        view.findViewById(R.id.btn_web_retry).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                errorView.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
                // 重试顺带清一次 WebView 缓存：网页的 JS/CSS 被缓存过时，
                // 光刷新页面拿到的是旧静态资源（比如新加的图表 resize 处理）。
                webView.clearCache(true);
                ensureLoaded(true);
            }
        });

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setAllowFileAccess(true);
        s.setSupportMultipleWindows(false);
        s.setJavaScriptCanOpenWindowsAutomatically(true);

        // 网页 → 原生 TTS（AI 助手回复播报）；懒初始化，网页没调 speak 就不会起引擎
        ttsBridge = new TtsBridge(requireContext().getApplicationContext());
        webView.addJavascriptInterface(ttsBridge, "AndroidTts");

        // 网页 → 原生语音输入（小慧对话框麦克风）；懒初始化，网页没点麦克风就不会碰麦克风。
        // 权限申请交给本 Fragment（只有它有 ActivityResult API），桥只负责"说要权限"。
        // 桥只做两件事：AudioRecord 录音（16k/单声道/16bit）→ POST /api/asr，
        // 由 PC 端的 Vosk 离线识别再回填输入框（不再依赖设备的语音服务）。
        voiceBridge = new VoiceBridge(requireContext().getApplicationContext(), webView, ttsBridge,
                new VoiceBridge.PermissionRequester() {
                    @Override
                    public void requestRecordAudio() {
                        recordAudioLauncher.launch(Manifest.permission.RECORD_AUDIO);
                    }
                });
        webView.addJavascriptInterface(voiceBridge, "AndroidStt");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                applyNightClass();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // 只处理主文档失败：页内某个图片/接口失败不该弹整页错误
                if (request != null && request.isForMainFrame()) {
                    progress.setVisibility(View.GONE);
                    errorView.setVisibility(View.VISIBLE);
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                try {
                    fileLauncher.launch(params.createIntent());
                    return true;
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
            }
        });

        backCallback = new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                // ⚠️ 隐藏中的网页 Tab 不许吞返回键（否则在首页按返回会去翻看不见的网页）
                if (isHidden() || webView == null || !webView.canGoBack()) {
                    setEnabled(false);
                    requireActivity().getOnBackPressedDispatcher().onBackPressed();
                    return;
                }
                webView.goBack();
            }
        };
        requireActivity().getOnBackPressedDispatcher()
                .addCallback(getViewLifecycleOwner(), backCallback);
    }

    @Override
    public void onStart() {
        super.onStart();
        if (!isHidden()) {
            ensureLoaded(false);
        }
    }

    @Override
    public void onHiddenChanged(boolean hidden) {
        super.onHiddenChanged(hidden);
        if (hidden) {
            // 切走时把麦克风关掉，别在看不见的 Tab 里继续听
            if (voiceBridge != null) voiceBridge.stop();
            return;
        }
        if (backCallback != null) {
            backCallback.setEnabled(true);      // 重新可见就能再拦返回键
        }
        ensureLoaded(false);
        if (loaded && webView != null) {
            // 切回时补一次 resize：ECharts 没有自己的尺寸监听，不补会画布错位
            webView.evaluateJavascript("window.dispatchEvent(new Event('resize'));", null);
            // 暗色模式可能在设置页被改过，切回时同步一次
            applyNightClass();
        }
    }

    /**
     * 把 App 的深色模式偏好同步到网页：给 &lt;html&gt; 加/去 {@code dark} 类
     * （style.css 里 {@code html.dark} 那套变量与覆盖规则负责换肤）。
     * 浏览器直连时没人调它，所以网页默认浅色。
     */
    private void applyNightClass() {
        if (webView == null) return;
        boolean dark = AppPrefs.isDarkMode(requireContext());
        webView.evaluateJavascript(
                "document.documentElement.classList.toggle('dark', " + dark + ");", null);
    }

    /** 首次可见才加载；服务器地址变了（force 或 base 不同）则重新加载 */
    private void ensureLoaded(boolean force) {
        if (webView == null) return;
        String base = ServerConfig.get(requireContext());
        if (!force && loaded && base.equals(loadedBase)) return;

        loaded = true;
        loadedBase = base;
        errorView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        webView.loadUrl(base + path);
    }

    @Override
    public void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    public void onPause() {
        if (voiceBridge != null) voiceBridge.stop();   // App 退到后台就别听了
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    public void onDestroyView() {
        if (voiceBridge != null) {
            voiceBridge.shutdown();
            voiceBridge = null;
        }
        if (ttsBridge != null) {
            ttsBridge.shutdown();
            ttsBridge = null;
        }
        if (webView != null) {
            webView.stopLoading();
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
        super.onDestroyView();
    }
}
