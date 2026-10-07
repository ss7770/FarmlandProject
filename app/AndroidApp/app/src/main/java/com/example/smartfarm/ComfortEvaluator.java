package com.example.smartfarm;

/**
 * 环境舒适度打分（2026-10-06 首页新增）：纯规则函数，无模型、无网络。
 *
 * <p>口径：温度/湿度/土壤各设一个「一般作物通用」适宜带，带内满分，
 * 带外按 falloff 线性衰减到 0；加权求和得 0~100 总分。
 * 标注为「综合评估」，<b>不是模型预测</b>——答辩口径与墒情模型失效的现状一致。
 *
 * <p>纯静态函数，无 Android 依赖，便于单测口算验证。
 */
public class ComfortEvaluator {

    public static final int LEVEL_GOOD = 0; // 优（绿）
    public static final int LEVEL_MID = 1;  // 良（橙）
    public static final int LEVEL_BAD = 2;  // 差（红）

    public static final int BAND_OK = 0;    // 适宜
    public static final int BAND_HIGH = 1;  // 偏高
    public static final int BAND_LOW = 2;   // 偏低

    /** 评估结果 */
    public static class Result {
        public int score;                        // 0~100
        public int level;                        // LEVEL_*
        public int tempBand, humiBand, soilBand; // BAND_*
    }

    // 适宜带与带外衰减距离（一般作物通用口径，现场可按作物调整）
    static final double TEMP_LO = 18, TEMP_HI = 28, TEMP_FALL = 10; // °C
    static final double HUMI_LO = 40, HUMI_HI = 70, HUMI_FALL = 25; // %RH
    static final double SOIL_LO = 40, SOIL_HI = 70, SOIL_FALL = 25; // %

    public static Result evaluate(double tempC, double humiPct, double soilPct) {
        Result r = new Result();
        double sTemp = subScore(tempC, TEMP_LO, TEMP_HI, TEMP_FALL);
        double sHumi = subScore(humiPct, HUMI_LO, HUMI_HI, HUMI_FALL);
        double sSoil = subScore(soilPct, SOIL_LO, SOIL_HI, SOIL_FALL);
        r.score = (int) Math.round(0.35 * sTemp + 0.30 * sHumi + 0.35 * sSoil);
        r.level = r.score >= 85 ? LEVEL_GOOD : (r.score >= 70 ? LEVEL_MID : LEVEL_BAD);
        r.tempBand = band(tempC, TEMP_LO, TEMP_HI);
        r.humiBand = band(humiPct, HUMI_LO, HUMI_HI);
        r.soilBand = band(soilPct, SOIL_LO, SOIL_HI);
        return r;
    }

    /** 带内 100 分；带外每偏离 falloff 距离扣 100 分，线性衰减到 0。 */
    private static double subScore(double v, double lo, double hi, double fall) {
        if (v >= lo && v <= hi) return 100;
        if (v < lo) return Math.max(0, 100 - (lo - v) / fall * 100);
        return Math.max(0, 100 - (v - hi) / fall * 100);
    }

    private static int band(double v, double lo, double hi) {
        if (v >= lo && v <= hi) return BAND_OK;
        return v > hi ? BAND_HIGH : BAND_LOW;
    }
}
