package com.example.smartfarm;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 网页 → 原生语音输入桥（2026-10-07 换路版：本机录音 + PC 端离线识别）。
 *
 * <p><b>为什么不再用安卓自带的语音识别</b>（上机实测，两条原生路都断了）：
 * <ol>
 *   <li>内嵌 {@code SpeechRecognizer}：每次 {@code startListening} 都被回
 *       {@code ERROR_RECOGNIZER_BUSY}——识别服务在、但干不了活（观感就是
 *       "先显示正在听，然后 E8"）；</li>
 *   <li>退回系统语音界面也不行：{@code RecognizerIntent} 抛
 *       {@code ActivityNotFoundException}——本机没有能处理它的界面。
 *       （已按官方文档确认包可见性不是原因：startActivity 不受可见性限制，
 *       补 {@code <queries>} 也没用。）</li>
 * </ol>
 *
 * <p><b>现在这条路</b>：本类只做两件事 —— 用 {@link AudioRecord} 把麦克风录成
 * 16kHz/16bit/单声道 PCM，拼成 WAV 后 POST 到 Flask 的 {@code /api/asr}，
 * 由 PC 上的 Vosk 离线识别，文本再回填网页输入框。
 * 好处：彻底绕开设备的语音服务，也不需要外网。<b>代价：没有"边说边上屏"</b>，
 * 录完才出结果。
 *
 * <p>几个约束：
 * <ul>
 *   <li><b>16k / 单声道 / 16bit</b>：Vosk 识别器创建时就定死采样率，改一端就得改两端。</li>
 *   <li><b>点一下开始、再点一下结束</b>：不做 VAD 自动断句，行为简单可预期；
 *       另外设 {@link #MAX_RECORD_MS} 上限，避免用户忘了点导致一直录。</li>
 *   <li><b>上传在后台线程</b>：{@code AudioRecord.read} 在录音线程，
 *       HTTP 在另一个线程，结果回主线程再 {@code evaluateJavascript}。</li>
 *   <li><b>权限交给宿主</b>：只有 Fragment 能申请权限，本类通过
 *       {@link PermissionRequester} 说"我要麦克风"。</li>
 * </ul>
 */
public class VoiceBridge {

    private static final String TAG = "VoiceBridge";

    /** Vosk 固定 16k：改这里必须同步改 blueprints/asr.py 与 App 端采样率 */
    private static final int SAMPLE_RATE = 16000;
    /** 单次录音上限：到点自动结束并识别，防止用户忘了点麦克风一直占着 */
    private static final int MAX_RECORD_MS = 15000;
    /** 短于这个时长直接判"没说话"，省一次上传 */
    private static final int MIN_RECORD_MS = 400;
    /** 读缓冲：0.1 秒 */
    private static final int READ_BUF_BYTES = 3200;

    /** 需要麦克风权限时由宿主（Fragment）去申请 */
    public interface PermissionRequester {
        void requestRecordAudio();
    }

    private final Context appContext;
    private final PermissionRequester requester;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    /** 释放时置空：shutdown 之后不许再碰 WebView */
    private WebView webView;
    private TtsBridge ttsBridge;

    private AudioRecord recorder;
    private Thread recordThread;
    private final ByteArrayOutputStream pcmBuf = new ByteArrayOutputStream();
    private volatile boolean recording;
    private long recordStartAt;

    /** 用户点了麦克风但当时没权限 ⇒ 授权通过后自动开录 */
    private boolean startPending;
    private boolean released;
    /** 录音超时自动结束 */
    private Runnable autoStop;

    public VoiceBridge(Context context, WebView webView, TtsBridge ttsBridge,
                       PermissionRequester requester) {
        this.appContext = context.getApplicationContext();
        this.webView = webView;
        this.ttsBridge = ttsBridge;
        this.requester = requester;
    }

    /* ==================== 网页调用入口 ==================== */

    /**
     * 本机能不能做语音输入。
     *
     * <p>换路之后这里恒为 true —— 我们只依赖"能不能录音"，而那是运行时权限的事，
     * 与设备有没有语音识别服务无关。保留这个方法是为了网页侧那句
     * {@code isAvailable()} 探测不用改（它只决定按钮是否加弱化样式）。
     */
    @JavascriptInterface
    public boolean isAvailable() {
        return true;
    }

    /** 开始录音；正在录时再调一次 = 结束并识别 */
    @JavascriptInterface
    public void start() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                startInternal();
            }
        });
    }

    /** 主动结束录音（等价于再点一下麦克风） */
    @JavascriptInterface
    public void stop() {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                stopRecording();
            }
        });
    }

    /** 权限弹窗关闭后由宿主调用（主线程） */
    public void onPermissionResult(boolean granted) {
        if (released) return;
        if (!startPending) return;
        startPending = false;
        if (!granted) {
            notifyError("permission");
            return;
        }
        startRecording();
    }

    /** Fragment 销毁时调用：停录音 + 释放麦克风 + 断开 WebView */
    public void shutdown() {
        released = true;
        startPending = false;
        cancelAutoStop();
        stopRecordingQuietly();
        webView = null;
        ttsBridge = null;
    }

    /* ==================== 内部实现 ==================== */

    private void startInternal() {
        if (released) return;
        if (recording) {
            stopRecording();          // 网页上再点一下 = 结束并识别
            return;
        }
        if (!hasRecordPermission()) {
            startPending = true;
            if (requester != null) {
                requester.requestRecordAudio();
            } else {
                startPending = false;
                notifyError("permission");
            }
            return;
        }
        startRecording();
    }

    private boolean hasRecordPermission() {
        return appContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void startRecording() {
        if (released || recording) return;
        // 别把"小慧"自己的播报录进去：开麦前先停 TTS
        if (ttsBridge != null) {
            ttsBridge.stopSpeaking();
        }
        int minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuf <= 0) {
            Log.w(TAG, "getMinBufferSize 失败，本机不支持 16k 单声道采集");
            notifyError("audio");
            return;
        }
        try {
            // VOICE_RECOGNITION 源带厂商的降噪/增益，比 MIC 更适合识别
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, READ_BUF_BYTES * 8));
        } catch (Exception e) {
            Log.w(TAG, "AudioRecord 创建失败：" + e);
            notifyError("audio");
            return;
        }
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            Log.w(TAG, "AudioRecord 未初始化（麦克风可能被别的应用占着）");
            releaseRecorder();
            notifyError("audio");
            return;
        }
        synchronized (pcmBuf) {
            pcmBuf.reset();
        }
        try {
            recorder.startRecording();
        } catch (Exception e) {
            Log.w(TAG, "startRecording 失败：" + e);
            releaseRecorder();
            notifyError("audio");
            return;
        }
        recording = true;
        recordStartAt = System.currentTimeMillis();
        recordThread = new Thread(new Runnable() {
            @Override
            public void run() {
                readLoop();
            }
        }, "voice-record");
        recordThread.start();

        notifyState(true);
        notifyVoicePhase("recording");
        Log.d(TAG, "开始录音（16k / mono / 16bit，最长 " + MAX_RECORD_MS + "ms）");
        scheduleAutoStop();
    }

    /** 录音线程：把 PCM 一段段攒进内存（短句几秒钟，几十 KB，放内存没问题） */
    private void readLoop() {
        byte[] buf = new byte[READ_BUF_BYTES];
        while (recording) {
            int n;
            try {
                n = recorder.read(buf, 0, buf.length);
            } catch (Exception e) {
                Log.w(TAG, "读音频出错，结束录音：" + e);
                break;
            }
            if (n > 0) {
                synchronized (pcmBuf) {
                    pcmBuf.write(buf, 0, n);
                }
            }
        }
    }

    /** 用户点停：结束录音 → 有内容就上传识别 */
    private void stopRecording() {
        if (!recording) return;
        long ms = System.currentTimeMillis() - recordStartAt;
        byte[] pcm = stopRecordingQuietly();
        if (pcm == null) return;

        notifyState(false);
        Log.d(TAG, "录音结束：" + ms + "ms / " + pcm.length + " 字节");
        if (ms < MIN_RECORD_MS || pcm.length < READ_BUF_BYTES) {
            notifyError("tooshort");
            return;
        }
        notifyVoicePhase("uploading");
        uploadAsync(pcm);
    }

    /**
     * 真正停掉录音并取走数据（幂等；shutdown 也走它）。
     *
     * @return 录到的 PCM；没有录音在进行时返回 null
     */
    private byte[] stopRecordingQuietly() {
        if (!recording && recorder == null) return null;
        boolean wasRecording = recording;
        recording = false;
        cancelAutoStop();
        try {
            if (recorder != null) recorder.stop();
        } catch (Exception ignored) {
            // 忽略：可能刚好已经自己停了
        }
        Thread t = recordThread;
        recordThread = null;
        if (t != null) {
            try {
                t.join(600);
            } catch (InterruptedException ignored) {
                // 忽略
            }
        }
        releaseRecorder();
        if (!wasRecording) return null;
        synchronized (pcmBuf) {
            return pcmBuf.toByteArray();
        }
    }

    /** 上传在后台线程，结果回主线程 */
    private void uploadAsync(final byte[] pcm) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                final byte[] wav = wrapWav(pcm);
                String text = null;
                String err = null;
                try {
                    text = postAsr(wav);
                } catch (Exception e) {
                    String msg = String.valueOf(e.getMessage());
                    err = msg.startsWith("unconfigured") ? "unconfigured" : "upload";
                    Log.w(TAG, "语音上传/识别失败：" + e);
                }
                final String fText = text;
                final String fErr = err;
                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        if (released) return;
                        if (fErr != null) {
                            notifyError(fErr);
                            return;
                        }
                        if (fText == null || fText.length() == 0) {
                            notifyError("nomatch");
                            return;
                        }
                        Log.d(TAG, "识别结果：" + fText);
                        notifyVoicePhase("done");
                        notifyResult(fText, true);
                    }
                });
            }
        }, "voice-upload").start();
    }

    /**
     * POST WAV 到 Flask 的 /api/asr。
     *
     * @return 识别文本（可能为空串 = 没听清）
     * @throws IOException 网络失败；其中模型未配置时 message 以 {@code unconfigured} 开头
     */
    private String postAsr(byte[] wav) throws Exception {
        HttpURLConnection conn = null;
        try {
            String base = ServerConfig.get(appContext);
            while (base.endsWith("/")) {     // 历史数据可能带尾斜杠，拼成 //api/asr 就 404 了
                base = base.substring(0, base.length() - 1);
            }
            URL url = new URL(base + "/api/asr");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(30000);          // 首次请求要加载模型，给足时间
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(wav.length);
            conn.setRequestProperty("Content-Type", "audio/wav");
            OutputStream os = conn.getOutputStream();
            os.write(wav);
            os.flush();
            os.close();

            int code = conn.getResponseCode();
            InputStream is = (code >= 400) ? conn.getErrorStream() : conn.getInputStream();
            String body = readAll(is);
            if (code == 503) {
                throw new IOException("unconfigured:" + body);
            }
            if (code >= 400) {
                throw new IOException("http_" + code + ":" + body);
            }
            JSONObject o = new JSONObject(body);
            if (!o.optBoolean("ok")) {
                throw new IOException("asr_err:" + body);
            }
            return o.optString("text", "");
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readAll(InputStream is) throws IOException {
        if (is == null) return "";
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[2048];
        int n;
        while ((n = is.read(buf)) > 0) {
            bos.write(buf, 0, n);
        }
        is.close();
        return new String(bos.toByteArray(), "UTF-8");
    }

    /** 给裸 PCM 套一个 44 字节的 WAV 头（Vosk 需要，也方便 PC 端用标准库解析） */
    private static byte[] wrapWav(byte[] pcm) {
        int dataLen = pcm.length;
        ByteArrayOutputStream out = new ByteArrayOutputStream(dataLen + 44);
        writeAscii(out, "RIFF");
        writeIntLE(out, 36 + dataLen);
        writeAscii(out, "WAVE");
        writeAscii(out, "fmt ");
        writeIntLE(out, 16);                     // fmt 块长度
        writeShortLE(out, (short) 1);            // 编码：PCM
        writeShortLE(out, (short) 1);            // 声道数：单声道
        writeIntLE(out, SAMPLE_RATE);            // 采样率
        writeIntLE(out, SAMPLE_RATE * 2);        // 字节率 = 采样率 × 声道 × 位深/8
        writeShortLE(out, (short) 2);            // 块对齐
        writeShortLE(out, (short) 16);           // 位深
        writeAscii(out, "data");
        writeIntLE(out, dataLen);
        out.write(pcm, 0, dataLen);
        return out.toByteArray();
    }

    private static void writeAscii(ByteArrayOutputStream out, String s) {
        for (int i = 0; i < s.length(); i++) {
            out.write(s.charAt(i) & 0xFF);
        }
    }

    private static void writeIntLE(ByteArrayOutputStream out, int v) {
        out.write(v & 0xFF);
        out.write((v >> 8) & 0xFF);
        out.write((v >> 16) & 0xFF);
        out.write((v >> 24) & 0xFF);
    }

    private static void writeShortLE(ByteArrayOutputStream out, short v) {
        out.write(v & 0xFF);
        out.write((v >> 8) & 0xFF);
    }

    private void scheduleAutoStop() {
        cancelAutoStop();
        autoStop = new Runnable() {
            @Override
            public void run() {
                autoStop = null;
                Log.d(TAG, "录音到上限 " + MAX_RECORD_MS + "ms，自动结束并识别");
                stopRecording();
            }
        };
        mainHandler.postDelayed(autoStop, MAX_RECORD_MS);
    }

    private void cancelAutoStop() {
        if (autoStop != null) {
            mainHandler.removeCallbacks(autoStop);
            autoStop = null;
        }
    }

    private void releaseRecorder() {
        if (recorder != null) {
            try {
                recorder.release();
            } catch (Exception ignored) {
                // 忽略
            }
            recorder = null;
        }
    }

    /* ==================== 回调网页 ==================== */

    private void notifyResult(String text, boolean isFinal) {
        if (text == null || text.length() == 0) return;
        // ⚠️ 必须 quote：识别文本可能含单引号/反斜杠/换行，直接拼会把 JS 拼坏
        notifyJs("window.onVoiceResult&&window.onVoiceResult("
                + JSONObject.quote(text) + "," + (isFinal ? "true" : "false") + ");");
    }

    private void notifyError(String code) {
        notifyJs("window.onVoiceError&&window.onVoiceError("
                + JSONObject.quote(code) + ",0);");
    }

    private void notifyState(boolean listening) {
        notifyJs("window.onVoiceState&&window.onVoiceState("
                + (listening ? "true" : "false") + ");");
    }

    /**
     * 录音阶段透传给网页：{@code recording}=正在录｜{@code uploading}=上传识别中｜{@code done}=出结果。
     * 因为这条路"录完才识别"，提示条必须说清当前在哪一步，否则用户以为卡住了。
     */
    private void notifyVoicePhase(String phase) {
        notifyJs("window.onVoicePhase&&window.onVoicePhase(" + JSONObject.quote(phase) + ");");
    }

    private void notifyJs(final String js) {
        final WebView view = webView;
        if (view == null) return;
        view.post(new Runnable() {
            @Override
            public void run() {
                try {
                    view.evaluateJavascript(js, null);
                } catch (Exception ignored) {
                    // WebView 已销毁等情况忽略
                }
            }
        });
    }
}
