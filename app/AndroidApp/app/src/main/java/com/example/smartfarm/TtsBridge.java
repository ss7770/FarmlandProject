package com.example.smartfarm;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.webkit.JavascriptInterface;

import java.util.Locale;

/**
 * 网页 → 原生 TTS 桥（2026-10-06）：把「AI 助手」的回复读出来。
 *
 * <p>为什么需要桥：安卓 WebView 里的 {@code speechSynthesis} 基本不可用，
 * 而原生 {@link TextToSpeech} 是现成的（病害播报 {@link DiseasePoller} 已在用，
 * 但那是另一条链路，本类独立，互不影响）。
 *
 * <p>用法：{@link WebTabFragment} 在 onViewCreated 里
 * {@code webView.addJavascriptInterface(new TtsBridge(appContext), "AndroidTts")}，
 * 网页侧 {@code if (window.AndroidTts) AndroidTts.speak(text)}；
 * onDestroyView 里调 {@link #shutdown()} 释放引擎。
 *
 * <p>几个刻意的设计：
 * <ul>
 *   <li><b>懒初始化</b>：只有真的调了 speak 才 new TextToSpeech——病害 Tab 不会白起一个引擎。</li>
 *   <li><b>开关实时读</b>：设置页「AI 助手语音」的开关每次 speak 时读 {@link AppPrefs}，
 *       网页侧不存状态，关掉立即静音。</li>
 *   <li><b>线程</b>：{@code @JavascriptInterface} 方法跑在 WebView 的 JavaBridge 线程，
 *       统一 post 到主线程操作 TTS。</li>
 *   <li><b>只播最新一条</b>：QUEUE_FLUSH，连点快捷问题不会排队念旧句子。</li>
 * </ul>
 */
public class TtsBridge {

    private final Context appContext;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private TextToSpeech tts;
    private volatile boolean ready;
    private boolean initializing;
    /** 引擎还没就绪时的最后一句话（只留最新一条） */
    private String pendingText;

    public TtsBridge(Context context) {
        this.appContext = context.getApplicationContext();
    }

    /** 网页调用入口（形如 AndroidTts.speak("...")） */
    @JavascriptInterface
    public void speak(String text) {
        if (text == null) return;
        final String t = text.trim();
        if (t.isEmpty()) return;
        if (!AppPrefs.isTtsEnabled(appContext)) return;   // 开关实时判断
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                speakInternal(t);
            }
        });
    }

    /** 供网页查询开关状态（可选用） */
    @JavascriptInterface
    public boolean isEnabled() {
        return AppPrefs.isTtsEnabled(appContext);
    }

    private void speakInternal(String text) {
        if (tts == null) {
            initTts(text);
            return;
        }
        if (!ready) {
            pendingText = text;      // 初始化还没完成，先记着
            return;
        }
        speakNow(text);
    }

    private void initTts(final String firstText) {
        if (initializing) {
            pendingText = firstText;
            return;
        }
        initializing = true;
        pendingText = firstText;
        tts = new TextToSpeech(appContext, new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                initializing = false;
                if (tts == null) {
                    ready = false;
                    return;
                }
                if (status == TextToSpeech.SUCCESS) {
                    int r = tts.setLanguage(Locale.CHINA);
                    ready = r != TextToSpeech.LANG_MISSING_DATA
                            && r != TextToSpeech.LANG_NOT_SUPPORTED;
                }
                if (ready && pendingText != null) {
                    String t = pendingText;
                    pendingText = null;
                    speakNow(t);
                }
            }
        });
    }

    private void speakNow(String text) {
        // 只播最新一条：AI 连答几句时不排队念旧的
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ai_" + System.currentTimeMillis());
    }

    /**
     * 停掉当前播报（2026-10-07 新增）：网页开始语音输入前由 {@link VoiceBridge} 调用。
     * 不停的话麦克风会把小慧自己的声音听进去，也会和识别抢音频通道。
     */
    public void stopSpeaking() {
        pendingText = null;          // 还没念的那句也一并取消
        if (tts != null) {
            try {
                tts.stop();
            } catch (Exception ignored) {
                // 引擎未就绪/已释放时忽略
            }
        }
    }

    /** Fragment 销毁时调用：停掉当前播报并释放引擎 */
    public void shutdown() {
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Exception ignored) {
                // 引擎已释放/未就绪时忽略
            }
            tts = null;
        }
        ready = false;
        initializing = false;
        pendingText = null;
    }
}
