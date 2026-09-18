package com.example.smartfarm;

/** 单次病害识别结果 */
public class DetectionResult {
    /** 英文原始标签（labels.txt 对应行） */
    public final String rawLabel;
    /** 中文病害名 */
    public final String diseaseName;
    /** 置信度 0~1 */
    public final float confidence;
    /** 防治建议（中文） */
    public final String suggestion;

    public DetectionResult(String rawLabel, String diseaseName, float confidence, String suggestion) {
        this.rawLabel = rawLabel;
        this.diseaseName = diseaseName;
        this.confidence = confidence;
        this.suggestion = suggestion;
    }
}
