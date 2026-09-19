package com.example.smartfarm;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 病害巡检轮询器（开发文档 v1.4 §4.1/4.2）：
 * - 每 30 秒 GET /api/disease/latest?after_id=lastId（后台线程，不阻塞 UI）
 * - 发现新记录 → 通知栏提醒 + TTS 中文播报（"检测到XX，置信度YY%，建议…"）
 * - lastId 存 SharedPreferences，重启后不重复通知旧记录
 * - 首次启动只同步游标，不把历史记录全推给用户
 */
public class DiseasePoller {

    private static final String TAG = "DiseasePoller";
    /** Flask 后端默认地址（P0：实际地址从 ServerConfig 读取，APP 内可改，不再硬编码） */
    public static final String SERVER_BASE = ServerConfig.DEFAULT_SERVER_BASE;
    /** 文档 v1.4：默认 30 秒轮询一次；演示时想更快可改小 */
    private static final long POLL_INTERVAL_MS = 30_000L;
    private static final String CHANNEL_ID = "disease_alert";
    private static final String PREFS = "disease_poll";
    private static final String KEY_LAST_ID = "last_id";

    /** 新记录回调（主线程），listener 可为 null */
    public interface Listener {
        void onNewRecords(long newLastId, String diseaseLabel, double confidence);
    }

    private final Context appContext;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final SharedPreferences prefs;
    private final Listener listener;

    private TextToSpeech tts;
    private volatile boolean ttsReady = false;
    private volatile boolean running = false;
    private volatile boolean busy = false;
    private long lastId;
    private boolean seeded;

    public DiseasePoller(Context context, Listener listener) {
        this.appContext = context.getApplicationContext();
        this.listener = listener;
        this.prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        this.lastId = prefs.getLong(KEY_LAST_ID, 0L);
        this.seeded = prefs.contains(KEY_LAST_ID);
        initTts();
    }

    private void initTts() {
        tts = new TextToSpeech(appContext, new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                if (status == TextToSpeech.SUCCESS) {
                    int r = tts.setLanguage(Locale.CHINA);
                    ttsReady = r != TextToSpeech.LANG_MISSING_DATA
                            && r != TextToSpeech.LANG_NOT_SUPPORTED;
                    if (!ttsReady) Log.w(TAG, "TTS 中文不可用，将只发通知");
                } else {
                    Log.w(TAG, "TTS 初始化失败，将只发通知");
                }
            }
        });
    }

    /** 开始轮询（在 Activity onCreate 里调用） */
    public synchronized void start() {
        if (running) return;
        running = true;
        mainHandler.post(pollRunnable);
        Log.i(TAG, "polling started, lastId=" + lastId);
    }

    /** 停止轮询并释放资源（在 Activity onDestroy 里调用） */
    public synchronized void stop() {
        running = false;
        mainHandler.removeCallbacks(pollRunnable);
        executor.shutdownNow();
        if (tts != null) {
            try { tts.stop(); tts.shutdown(); } catch (Exception ignored) {}
            tts = null;
        }
        Log.i(TAG, "polling stopped");
    }

    private final Runnable pollRunnable = new Runnable() {
        @Override
        public void run() {
            if (!running) return;
            if (!busy) {
                busy = true;
                executor.execute(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            pollOnce();
                        } catch (Exception e) {
                            Log.w(TAG, "poll failed: " + e.getMessage());
                        } finally {
                            busy = false;
                        }
                    }
                });
            }
            mainHandler.postDelayed(this, POLL_INTERVAL_MS);
        }
    };

    private void pollOnce() throws Exception {
        URL url = new URL(ServerConfig.get(appContext) + "/api/disease/latest?after_id=" + lastId);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(5000);
        conn.setRequestMethod("GET");
        int code = conn.getResponseCode();
        if (code != 200) { conn.disconnect(); return; }

        String body = readAll(conn.getInputStream());
        conn.disconnect();

        JSONObject root = new JSONObject(body);
        JSONArray records = root.optJSONArray("records");
        if (records == null || records.length() == 0) return;

        long maxId = lastId;
        JSONObject latest = null;
        for (int i = 0; i < records.length(); i++) {
            JSONObject rec = records.getJSONObject(i);
            long id = rec.optLong("id", 0L);
            if (id > maxId) { maxId = id; latest = rec; }
        }
        if (maxId <= lastId || latest == null) return;

        boolean firstSync = !seeded;   // 首次启动：只同步游标，不轰炸历史记录
        lastId = maxId;
        seeded = true;
        prefs.edit().putLong(KEY_LAST_ID, lastId).apply();

        if (firstSync) return;

        final String label = latest.optString("disease", "未知病害");
        final double confidence = latest.optDouble("confidence", 0.0);
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                notifyAndSpeak(lastId, label, confidence);
                if (listener != null) listener.onNewRecords(lastId, label, confidence);
            }
        });
    }

    /** 通知栏 + TTS（文档 v1.4 §4.2） */
    private void notifyAndSpeak(long id, String label, double confidence) {
        String name = DiseaseKnowledge.getChineseName(label);
        if (name == null || name.length() == 0) name = label; // 手动记录可能已是中文
        String suggestion = DiseaseKnowledge.getSuggestion(label);
        int pct = (int) Math.round(confidence);
        if (confidence > 0 && confidence <= 1.0) pct = (int) Math.round(confidence * 100);

        String title = "⚠️ 病害巡检提醒";
        String text = "检测到" + name + "，置信度 " + pct + "%";

        NotificationManager nm =
                (NotificationManager) appContext.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;

        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "病害巡检提醒",
                    NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("ESP32-CAM 自动巡检发现的病害通知");
            nm.createNotificationChannel(ch);
        }

        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(appContext, CHANNEL_ID)
                : new Notification.Builder(appContext);
        b.setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(
                        text + "\n" + (suggestion == null ? "" : suggestion)))
                .setAutoCancel(true);

        Intent click = new Intent(appContext, DiseaseActivity.class);
        click.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        b.setContentIntent(PendingIntent.getActivity(appContext, (int) id, click,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));

        try {
            nm.notify((int) id, b.build());
        } catch (Exception e) {
            Log.w(TAG, "notify failed: " + e.getMessage());
        }

        if (ttsReady && tts != null) {
            String speech = "检测到" + name + "，置信度" + pct + "%，"
                    + (suggestion == null ? "建议及时防治" : suggestion);
            tts.speak(speech, TextToSpeech.QUEUE_ADD, null, "disease_" + id);
        }
    }

    private static String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }
}
