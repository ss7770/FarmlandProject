package com.example.smartfarm;

import android.content.Context;
import android.graphics.Bitmap;
import android.util.Log;

import org.tensorflow.lite.Interpreter;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.util.ArrayList;
import java.util.List;

/**
 * 病害识别推理器。
 * 模型：assets/model.tflite（MobileNetV2，输入 224x224x3 float32 [0,1]，输出 38 维 logits）
 * 标签：assets/labels.txt（38 行，顺序即输出索引）
 */
public class DiseaseDetector {
    private static final String TAG = "DiseaseDetector";
    private static final String MODEL_FILE = "model.tflite";
    private static final String LABEL_FILE = "labels.txt";
    private static final int INPUT_SIZE = 224;

    private Interpreter interpreter;
    private final List<String> labels = new ArrayList<>();

    /** 加载模型与标签，耗时操作，请在子线程调用 */
    public void loadModel(Context context) throws IOException {
        if (interpreter != null) {
            return;
        }
        loadLabels(context);
        MappedByteBuffer model = loadModelFile(context);
        Interpreter.Options options = new Interpreter.Options();
        options.setNumThreads(4);
        interpreter = new Interpreter(model, options);
        Log.i(TAG, "TFLite model loaded, labels=" + labels.size());
    }

    public boolean isReady() {
        return interpreter != null;
    }

    /** 识别一张叶片图，耗时操作，请在子线程调用 */
    public DetectionResult detect(Bitmap bitmap) {
        if (interpreter == null) {
            throw new IllegalStateException("Model not loaded, call loadModel() first");
        }

        Bitmap scaled = Bitmap.createScaledBitmap(bitmap, INPUT_SIZE, INPUT_SIZE, true);
        ByteBuffer input = preprocess(scaled);

        float[][] output = new float[1][labels.size()];
        interpreter.run(input, output);

        // logits -> softmax 概率
        int n = labels.size();
        float maxLogit = Float.NEGATIVE_INFINITY;
        for (int i = 0; i < n; i++) {
            maxLogit = Math.max(maxLogit, output[0][i]);
        }
        float sum = 0f;
        float[] probs = new float[n];
        for (int i = 0; i < n; i++) {
            probs[i] = (float) Math.exp(output[0][i] - maxLogit);
            sum += probs[i];
        }
        int best = 0;
        for (int i = 1; i < n; i++) {
            if (probs[i] > probs[best]) {
                best = i;
            }
        }
        float confidence = probs[best] / sum;

        String rawLabel = labels.get(best);
        String zhName = DiseaseKnowledge.getChineseName(rawLabel);
        String suggestion = DiseaseKnowledge.getSuggestion(rawLabel);
        if (zhName == null) {
            zhName = rawLabel.replace("___", " · ").replace("_", " ");
        }
        if (suggestion == null) {
            suggestion = "暂无该病害的建议数据，请咨询当地农技人员。";
        }
        return new DetectionResult(rawLabel, zhName, confidence, suggestion);
    }

    /** ARGB_8888 -> float [0,1]，NHWC */
    private ByteBuffer preprocess(Bitmap scaled) {
        ByteBuffer buffer = ByteBuffer.allocateDirect(1 * INPUT_SIZE * INPUT_SIZE * 3 * 4);
        buffer.order(ByteOrder.nativeOrder());
        int[] pixels = new int[INPUT_SIZE * INPUT_SIZE];
        scaled.getPixels(pixels, 0, INPUT_SIZE, 0, 0, INPUT_SIZE, INPUT_SIZE);
        for (int pixel : pixels) {
            float r = ((pixel >> 16) & 0xFF) / 255f;
            float g = ((pixel >> 8) & 0xFF) / 255f;
            float b = (pixel & 0xFF) / 255f;
            buffer.putFloat(r);
            buffer.putFloat(g);
            buffer.putFloat(b);
        }
        buffer.rewind();
        return buffer;
    }

    private void loadLabels(Context context) throws IOException {
        labels.clear();
        BufferedReader reader = null;
        try {
            reader = new BufferedReader(new InputStreamReader(context.getAssets().open(LABEL_FILE), "UTF-8"));
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty()) {
                    labels.add(line);
                }
            }
        } finally {
            if (reader != null) {
                reader.close();
            }
        }
        if (labels.size() == 0) {
            throw new IOException("labels.txt is empty");
        }
    }

    private MappedByteBuffer loadModelFile(Context context) throws IOException {
        java.io.FileInputStream fis = null;
        java.nio.channels.FileChannel channel = null;
        try {
            java.io.InputStream is = context.getAssets().open(MODEL_FILE);
            // 先拷贝到缓存文件，便于 mmap 加载
            java.io.File cacheFile = new java.io.File(context.getCacheDir(), MODEL_FILE);
            java.io.FileOutputStream fos = new java.io.FileOutputStream(cacheFile);
            byte[] buf = new byte[8192];
            int len;
            while ((len = is.read(buf)) != -1) {
                fos.write(buf, 0, len);
            }
            fos.flush();
            fos.close();
            is.close();

            fis = new java.io.FileInputStream(cacheFile);
            channel = fis.getChannel();
            return channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size());
        } finally {
            if (channel != null) {
                channel.close();
            }
            if (fis != null) {
                fis.close();
            }
        }
    }

    /** 释放资源 */
    public void close() {
        if (interpreter != null) {
            interpreter.close();
            interpreter = null;
        }
    }
}
