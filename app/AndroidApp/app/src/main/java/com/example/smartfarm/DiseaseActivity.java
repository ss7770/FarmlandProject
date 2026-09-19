package com.example.smartfarm;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.speech.tts.TextToSpeech;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 图像识别看板（需求文档 2026-09-18 §2，v2）：
 * - 区域1：最近拍摄图片（优先取最新识别记录；无记录时兜底取 uploads/ 最新图片）
 * - 区域2：当前识别结果（病害中文名 / 置信度 / 防治建议）
 * - 区域3：[本地上传]（相册选图）[拍照识别]（系统相机拍照）
 *   两者的图都会 POST /api/upload_image，APP 直接解析服务端响应立即显示结果，
 *   不再依赖“拉最新记录”（置信度低于 60% 不入库时也能看到识别结果）
 * - P1 断网自治：服务端不可达时自动降级本地 TFLite 离线识别（DiseaseDetector）
 */
public class DiseaseActivity extends AppCompatActivity {

    private static final String TAG = "DiseaseActivity";
    private static final int REQUEST_PICK_IMAGE = 101;
    private static final int REQUEST_READ_STORAGE = 102;
    private static final int REQUEST_TAKE_PHOTO = 103;
    private static final int REQUEST_CAMERA = 104;
    /** 页面内看板刷新间隔（文档 §2.3） */
    private static final long REFRESH_INTERVAL_MS = 30_000L;
    /** P2 拒识三档阈值（与服务端 capture.py 一致，单位 %） */
    private static final double VERDICT_CONFIRMED_PCT = 75.0;
    private static final double VERDICT_SUSPECTED_PCT = 45.0;

    private ImageView ivImage;
    private TextView tvEmpty;
    private Button btnPick, btnTake;
    private LinearLayout cardResult;
    private TextView tvName, tvConfidence, tvSuggestion, tvStatus;

    private TextToSpeech tts;
    private boolean ttsReady = false;

    /** 当前展示图片的 JPEG 字节，供上传识别使用 */
    private byte[] currentImageBytes;
    /** 拍照临时文件 */
    private File cameraFile;
    /** 页面已展示的最新记录 id，用于 30 秒轮询判新 */
    private long lastSeenId = 0;
    private boolean seenAny = false;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean refreshing = false;

    /** 本地 TFLite 离线识别器（P1：服务端不可达时的断网备份） */
    private DiseaseDetector offlineDetector;
    private volatile boolean offlineLoading = false;

    private final Runnable refreshRunnable = new Runnable() {
        @Override
        public void run() {
            loadLatestRecord(false);
            mainHandler.postDelayed(this, REFRESH_INTERVAL_MS);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_disease);

        initViews();
        initTts();
        loadLatestRecord(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        mainHandler.postDelayed(refreshRunnable, REFRESH_INTERVAL_MS);
    }

    @Override
    protected void onPause() {
        super.onPause();
        mainHandler.removeCallbacks(refreshRunnable);
    }

    private void initViews() {
        ivImage = findViewById(R.id.iv_disease_image);
        tvEmpty = findViewById(R.id.tv_disease_empty);
        btnPick = findViewById(R.id.btn_pick_image);
        btnTake = findViewById(R.id.btn_take_photo);
        cardResult = findViewById(R.id.card_result);
        tvName = findViewById(R.id.tv_disease_name);
        tvConfidence = findViewById(R.id.tv_disease_confidence);
        tvSuggestion = findViewById(R.id.tv_disease_suggestion);
        tvStatus = findViewById(R.id.tv_disease_status);

        btnPick.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                pickImage();
            }
        });

        btnTake.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                takePhoto();
            }
        });
    }

    private void initTts() {
        tts = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                if (status == TextToSpeech.SUCCESS) {
                    int result = tts.setLanguage(Locale.CHINA);
                    ttsReady = (result != TextToSpeech.LANG_MISSING_DATA
                            && result != TextToSpeech.LANG_NOT_SUPPORTED);
                }
            }
        });
    }

    // ---------- 服务端数据 ----------

    /** 拉取最新一条识别记录刷新看板；无记录时兜底显示 uploads/ 最新图片 */
    private void loadLatestRecord(final boolean first) {
        if (refreshing) return;
        refreshing = true;
        new Thread(new Runnable() {
            @Override
            public void run() {
                JSONObject record = null;
                String err = null;
                try {
                    HttpURLConnection conn = (HttpURLConnection)
                            new URL(ServerConfig.get(DiseaseActivity.this) + "/api/disease/records?limit=1")
                                    .openConnection();
                    conn.setConnectTimeout(4000);
                    conn.setReadTimeout(4000);
                    int code = conn.getResponseCode();
                    if (code == 200) {
                        JSONObject root = new JSONObject(readAll(conn.getInputStream()));
                        JSONArray arr = root.optJSONArray("records");
                        if (arr != null && arr.length() > 0) {
                            record = arr.getJSONObject(0);
                        }
                    } else {
                        err = "HTTP " + code;
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    err = e.getMessage();
                }
                final JSONObject rec = record;
                final String error = err;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshing = false;
                        applyLatestRecord(rec, error, first);
                    }
                });
            }
        }).start();
    }

    private void applyLatestRecord(JSONObject rec, String error, boolean first) {
        if (error != null) {
            if (first) tvStatus.setText("后端连接失败：" + error);
            return;
        }
        if (rec == null) {
            // 没有任何识别记录：兜底显示 uploads/ 里最新的巡检图片
            lastSeenId = 0;
            seenAny = false;
            tvStatus.setText(R.string.disease_no_record);
            fetchLatestUploadImage();
            return;
        }

        long id = rec.optLong("id", 0);
        if (seenAny && id <= lastSeenId) return;   // 没有新记录
        boolean hasNew = seenAny && id > lastSeenId;
        lastSeenId = id;
        seenAny = true;

        if (!hasNew && !first && currentImageBytes != null) {
            // 仅同步游标，不覆盖用户正在看的图
            return;
        }

        String imagePath = rec.optString("image_path", "");
        String disease = rec.optString("disease", "");
        double confidence = rec.optDouble("confidence", 0);

        if (imagePath.length() > 0) {
            downloadImage(imagePath);
        } else {
            fetchLatestUploadImage();
        }
        // 历史记录可能建于旧阈值（60%）时期：按当前三档规则归档展示
        double pct = confidence > 0 && confidence <= 1.0 ? confidence * 100.0 : confidence;
        String verdict = pct >= VERDICT_CONFIRMED_PCT ? "confirmed" : "suspected";
        showRecordResult(disease, confidence, verdict, hasNew || first);
    }

    /** GET /api/uploads/latest：无识别记录时兜底显示最近巡检/预置图片 + 其识别结果 */
    private void fetchLatestUploadImage() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                String name = null;
                String label = null;
                double confidence = 0;
                String verdict = "confirmed";
                try {
                    HttpURLConnection conn = (HttpURLConnection)
                            new URL(ServerConfig.get(DiseaseActivity.this) + "/api/uploads/latest")
                                    .openConnection();
                    conn.setConnectTimeout(4000);
                    conn.setReadTimeout(60000);   // 首次可能触发服务端推理
                    if (conn.getResponseCode() == 200) {
                        JSONObject root = new JSONObject(readAll(conn.getInputStream()));
                        if ("ok".equals(root.optString("status"))) {
                            name = root.optString("name", null);
                            if (root.has("disease")) {
                                label = root.optString("disease", "");
                                confidence = root.optDouble("confidence", 0);
                                verdict = root.optString("verdict", "confirmed");
                            }
                        }
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    Log.w(TAG, "latest upload query failed: " + e.getMessage());
                }
                final String fName = name;
                final String fLabel = label;
                final double fConfidence = confidence;
                final String fVerdict = verdict;
                if (fName != null) {
                    downloadImage("/api/uploads/" + fName);
                }
                if (fLabel != null && fLabel.length() > 0) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            showRecordResult(fLabel, fConfidence, fVerdict, false);
                            tvStatus.setText("rejected".equals(fVerdict)
                                    ? "最近图片已拒识（未写入巡检记录）"
                                    : "最近图片识别结果（未写入巡检记录）");
                        }
                    });
                }
            }
        }).start();
    }

    private void downloadImage(final String imagePath) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String name = imagePath.contains("/")
                            ? imagePath.substring(imagePath.lastIndexOf('/') + 1)
                            : imagePath;
                    URL url = new URL(ServerConfig.get(DiseaseActivity.this) + "/api/uploads/" + name);
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    byte[] data = null;
                    if (conn.getResponseCode() == 200) {
                        data = readBytes(conn.getInputStream());
                    }
                    conn.disconnect();
                    final byte[] bytes = data;
                    if (bytes != null) {
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                Bitmap bmp = decodeSampled(bytes, 1024);
                                if (bmp != null) {
                                    ivImage.setImageBitmap(bmp);
                                    tvEmpty.setVisibility(View.GONE);
                                }
                            }
                        });
                    }
                } catch (Exception e) {
                    Log.w(TAG, "download image failed: " + e.getMessage());
                }
            }
        }).start();
    }

    /** 用服务器记录渲染"当前识别结果"卡片（confirmed 档） */
    private void showRecordResult(String label, double confidence, boolean speakIt) {
        showRecordResult(label, confidence, "confirmed", speakIt);
    }

    /**
     * 按 P2 三档置信度渲染结果卡片。
     * verdict: confirmed（≥75%，确诊）/ suspected（45~75%，疑似建议重拍）/ rejected（<45%，拒识）
     */
    private void showRecordResult(String label, double confidence, String verdict, boolean speakIt) {
        String name = DiseaseKnowledge.getChineseName(label);
        if (name == null || name.length() == 0) name = label;
        String suggestion = DiseaseKnowledge.getSuggestion(label);
        int percent = (int) Math.round(confidence);
        if (confidence > 0 && confidence <= 1.0) percent = (int) Math.round(confidence * 100);
        boolean healthy = label.endsWith("healthy");

        cardResult.setVisibility(View.VISIBLE);
        tvStatus.setText("");

        if ("rejected".equals(verdict)) {
            tvName.setText("无法可靠识别");
            tvConfidence.setText(String.format(getString(R.string.disease_confidence_fmt), percent));
            tvConfidence.setTextColor(getResources().getColor(R.color.colorError));
            tvSuggestion.setText("最高候选：" + name
                    + "。未匹配到可靠的叶片病害特征，请对准叶片、保证光照充足后重拍；非叶片目标将被拒识。");
            if (speakIt && ttsReady && tts != null) {
                speak("未能可靠识别，请对准叶片重新拍摄");
            }
            return;
        }

        if ("suspected".equals(verdict)) {
            tvName.setText("疑似：" + name);
            tvConfidence.setText(String.format(getString(R.string.disease_confidence_fmt), percent));
            tvConfidence.setTextColor(getResources().getColor(R.color.colorWarning));
            tvSuggestion.setText(getString(R.string.disease_suggestion_prefix)
                    + (suggestion == null ? "暂无防治建议" : suggestion)
                    + "（疑似结果，建议多角度重拍确认后再处理）");
            if (speakIt && ttsReady && tts != null) {
                speak("疑似检测到" + name + "，置信度" + percent + "%，建议重拍确认");
            }
            return;
        }

        // confirmed：确诊档
        tvName.setText(name);
        tvConfidence.setText(String.format(getString(R.string.disease_confidence_fmt), percent));
        tvConfidence.setTextColor(getResources().getColor(
                healthy ? R.color.colorSuccess : R.color.colorWarning));
        tvSuggestion.setText(getString(R.string.disease_suggestion_prefix)
                + (suggestion == null ? "暂无防治建议" : suggestion));

        if (speakIt && ttsReady && tts != null) {
            String ttsText = DiseaseKnowledge.buildTtsText(label);
            if (ttsText == null) {
                ttsText = "识别结果：" + name + "，置信度" + percent + "%";
            }
            speak(ttsText);
        }
    }

    // ---------- 本地上传 ----------

    private void pickImage() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.READ_EXTERNAL_STORAGE}, REQUEST_READ_STORAGE);
            return;
        }
        Intent intent = new Intent(Intent.ACTION_PICK,
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI);
        intent.setType("image/*");
        startActivityForResult(intent, REQUEST_PICK_IMAGE);
    }

    // ---------- 拍照 ----------

    private void takePhoto() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                    new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA);
            return;
        }
        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        if (intent.resolveActivity(getPackageManager()) == null) {
            Toast.makeText(this, "未找到可用相机应用", Toast.LENGTH_SHORT).show();
            return;
        }
        File dir = getExternalFilesDir(Environment.DIRECTORY_PICTURES);
        if (dir != null && !dir.exists()) {
            dir.mkdirs();
        }
        cameraFile = new File(dir, "photo_"
                + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.CHINA).format(new Date()) + ".jpg");
        Uri photoUri = FileProvider.getUriForFile(this,
                getPackageName() + ".fileprovider", cameraFile);
        intent.putExtra(MediaStore.EXTRA_OUTPUT, photoUri);
        intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        startActivityForResult(intent, REQUEST_TAKE_PHOTO);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_READ_STORAGE) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                Intent intent = new Intent(Intent.ACTION_PICK,
                        MediaStore.Images.Media.EXTERNAL_CONTENT_URI);
                intent.setType("image/*");
                startActivityForResult(intent, REQUEST_PICK_IMAGE);
            } else {
                Toast.makeText(this, R.string.disease_need_storage, Toast.LENGTH_SHORT).show();
            }
        } else if (requestCode == REQUEST_CAMERA) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                takePhoto();
            } else {
                Toast.makeText(this, "需要相机权限才能拍照识别", Toast.LENGTH_SHORT).show();
            }
        }
    }

    /** 图片就绪后的公共流程：显示 → 压缩 → 上传识别 */
    private void onImageReady(Bitmap bmp) {
        ivImage.setImageBitmap(bmp);
        tvEmpty.setVisibility(View.GONE);
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.JPEG, 85, bos);
        currentImageBytes = bos.toByteArray();
        uploadCurrentImage();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, @Nullable Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_PICK_IMAGE && resultCode == RESULT_OK && data != null
                && data.getData() != null) {
            Bitmap bmp = decodeSampled(data.getData(), 1024);
            if (bmp == null) {
                Toast.makeText(this, R.string.disease_decode_failed, Toast.LENGTH_SHORT).show();
                return;
            }
            onImageReady(bmp);
        } else if (requestCode == REQUEST_TAKE_PHOTO && resultCode == RESULT_OK) {
            if (cameraFile == null || !cameraFile.exists()) {
                Toast.makeText(this, R.string.disease_decode_failed, Toast.LENGTH_SHORT).show();
                return;
            }
            byte[] raw = readBytesQuietly(cameraFile);
            Bitmap bmp = raw == null ? null : decodeSampled(raw, 1024);
            if (bmp == null) {
                Toast.makeText(this, R.string.disease_decode_failed, Toast.LENGTH_SHORT).show();
                return;
            }
            onImageReady(bmp);
            cameraFile.delete();   // 已压缩进内存，临时文件即删
        }
    }

    private static byte[] readBytesQuietly(File file) {
        try {
            java.io.FileInputStream fis = new java.io.FileInputStream(file);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = fis.read(buf)) > 0) out.write(buf, 0, n);
            fis.close();
            return out.toByteArray();
        } catch (Exception e) {
            return null;
        }
    }

    // ---------- 上传识别（直接解析服务端响应） ----------

    /** 把当前图片按 multipart/form-data 上传到 /api/upload_image，解析响应立即显示结果 */
    private void uploadCurrentImage() {
        if (currentImageBytes == null) {
            Toast.makeText(this, R.string.disease_pick_first, Toast.LENGTH_SHORT).show();
            return;
        }
        btnPick.setEnabled(false);
        btnTake.setEnabled(false);
        tvStatus.setText(R.string.disease_detecting);
        cardResult.setVisibility(View.GONE);

        final byte[] payload = currentImageBytes;
        new Thread(new Runnable() {
            @Override
            public void run() {
                String error = null;
                String respBody = null;
                try {
                    respBody = multipartUpload(ServerConfig.get(DiseaseActivity.this), payload);
                } catch (Exception e) {
                    Log.e(TAG, "upload failed", e);
                    error = e.getMessage();
                }
                final String err = error;
                final String resp = respBody;
                final byte[] payloadBytes = payload;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        btnPick.setEnabled(true);
                        btnTake.setEnabled(true);
                        if (err != null) {
                            // P1：服务端不可达 → 自动降级本地 TFLite 离线识别
                            tvStatus.setText("服务端不可达（" + err + "），切换本地离线识别…");
                            runOfflineDetect(payloadBytes);
                            return;
                        }
                        applyUploadResponse(resp);
                    }
                });
            }
        }).start();
    }

    /** 解析 /api/upload_image 的 JSON 响应，按三档置信度渲染结果（不等/不依赖写库） */
    private void applyUploadResponse(String body) {
        try {
            JSONObject root = new JSONObject(body);
            if (!"ok".equals(root.optString("status"))) {
                tvStatus.setText("服务端返回：" + root.optString("msg", "未知错误"));
                return;
            }
            String msg = root.optString("msg", "");
            if (root.has("disease")) {
                String label = root.optString("disease", "");
                double confidence = root.optDouble("confidence", 0);
                String verdict = root.optString("verdict", "");
                if (verdict.length() == 0) {
                    // 兼容旧服务端：仅按"not recorded"标记降级为疑似档
                    verdict = msg.contains("not recorded") ? "suspected" : "confirmed";
                }
                showRecordResult(label, confidence, verdict, true);
                if ("confirmed".equals(verdict)) {
                    tvStatus.setText("识别完成（已写入巡检记录）");
                } else if ("suspected".equals(verdict)) {
                    tvStatus.setText("疑似病害：结果仅供参考，建议重拍确认（未写入巡检记录）");
                } else {
                    tvStatus.setText("已拒识：未匹配到可靠叶片病害（未写入巡检记录）");
                }
            } else {
                tvStatus.setText("图片已保存：" + root.optString("saved", "")
                        + (msg.length() > 0 ? "（" + msg + "）" : ""));
            }
        } catch (Exception e) {
            Log.w(TAG, "parse upload response failed", e);
            tvStatus.setText("响应解析失败：" + body);
        }
    }

    // ---------- 本地离线识别（P1：断网备份） ----------

    /**
     * 服务端不可达时用本地 TFLite（assets/model.tflite，MobileNetV2 38 类）识别。
     * 模型懒加载一次常驻；识别结果走与在线一致的展示/播报链路，仅状态栏标注"离线"。
     */
    private void runOfflineDetect(final byte[] imageBytes) {
        if (offlineLoading) return;
        offlineLoading = true;
        new Thread(new Runnable() {
            @Override
            public void run() {
                Exception failure = null;
                DetectionResult result = null;
                try {
                    if (offlineDetector == null) {
                        DiseaseDetector detector = new DiseaseDetector();
                        detector.loadModel(DiseaseActivity.this);   // 首次约 1~2 秒
                        offlineDetector = detector;
                    }
                    Bitmap bmp = decodeSampled(imageBytes, 640);
                    if (bmp == null) {
                        failure = new IllegalStateException("图片解码失败");
                    } else {
                        result = offlineDetector.detect(bmp);
                    }
                } catch (Exception e) {
                    Log.e(TAG, "offline detect failed", e);
                    failure = e;
                } finally {
                    offlineLoading = false;
                }
                final DetectionResult r = result;
                final Exception e = failure;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (e != null || r == null) {
                            tvStatus.setText("离线识别不可用："
                                    + (e == null ? "未知错误" : e.getMessage()));
                            return;
                        }
                        // 本地同样执行 P2 三档拒识（阈值与服务端一致）
                        double pct = r.confidence * 100.0;
                        String verdict = pct >= VERDICT_CONFIRMED_PCT ? "confirmed"
                                : (pct >= VERDICT_SUSPECTED_PCT ? "suspected" : "rejected");
                        showRecordResult(r.rawLabel, r.confidence, verdict, true);
                        if ("rejected".equals(verdict)) {
                            tvStatus.setText("离线拒识：未匹配到可靠叶片病害（本地 TFLite）");
                        } else if ("suspected".equals(verdict)) {
                            tvStatus.setText("离线识别完成（疑似，本地 TFLite，服务端未联网）");
                        } else {
                            tvStatus.setText("离线识别完成（本地 TFLite，服务端未联网）");
                        }
                    }
                });
            }
        }).start();
    }

    private static String multipartUpload(String baseUrl, byte[] image) throws Exception {
        String boundary = "----SmartFarmAppBoundary9f2c";
        String prefix = "--" + boundary +
                "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"upload.jpg\"\r\n" +
                "Content-Type: image/jpeg\r\n\r\n";
        byte[] prefixBytes = prefix.getBytes(StandardCharsets.UTF_8);
        byte[] suffixBytes = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);

        HttpURLConnection conn = (HttpURLConnection)
                new URL(baseUrl + "/api/upload_image").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(60000);   // 服务端推理 + 冷启动需要时间
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        conn.setFixedLengthStreamingMode(prefixBytes.length + image.length + suffixBytes.length);
        java.io.OutputStream os = conn.getOutputStream();
        os.write(prefixBytes);
        os.write(image);
        os.write(suffixBytes);
        os.flush();
        os.close();
        int code = conn.getResponseCode();
        String body = code == 200 ? readAll(conn.getInputStream()) : "";
        conn.disconnect();
        if (code != 200) throw new IllegalStateException("HTTP " + code + " " + body);
        return body;
    }

    // ---------- 工具 ----------

    /** 按目标尺寸降采样解码 byte[]，避免大图 OOM */
    private Bitmap decodeSampled(byte[] data, int maxDim) {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(data, 0, data.length, options);
        int sample = 1;
        while (Math.max(options.outWidth, options.outHeight) / (sample * 2) >= maxDim) {
            sample *= 2;
        }
        options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        return BitmapFactory.decodeByteArray(data, 0, data.length, options);
    }

    /** 按目标尺寸降采样解码 Uri */
    private Bitmap decodeSampled(Uri uri, int maxDim) {
        try {
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inJustDecodeBounds = true;
            InputStream is = getContentResolver().openInputStream(uri);
            BitmapFactory.decodeStream(is, null, options);
            if (is != null) {
                is.close();
            }
            int sample = 1;
            while (Math.max(options.outWidth, options.outHeight) / (sample * 2) >= maxDim) {
                sample *= 2;
            }
            options = new BitmapFactory.Options();
            options.inSampleSize = sample;
            is = getContentResolver().openInputStream(uri);
            Bitmap bmp = BitmapFactory.decodeStream(is, null, options);
            if (is != null) {
                is.close();
            }
            return bmp;
        } catch (Exception e) {
            Log.e(TAG, "decode failed", e);
            return null;
        }
    }

    private void speak(String text) {
        if (ttsReady && tts != null) {
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "disease_board");
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

    private static byte[] readBytes(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toByteArray();
    }

    @Override
    protected void onDestroy() {
        mainHandler.removeCallbacks(refreshRunnable);
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Exception ignored) {
            }
        }
        if (offlineDetector != null) {
            offlineDetector.close();
            offlineDetector = null;
        }
        super.onDestroy();
    }
}
