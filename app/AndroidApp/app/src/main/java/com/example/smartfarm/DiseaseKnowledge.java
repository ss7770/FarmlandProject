package com.example.smartfarm;

import java.util.HashMap;
import java.util.Map;

/**
 * PlantVillage 38 类病害知识库（中文名 + 防治建议）。
 * 标签顺序与 assets/labels.txt 行序一致，标签文本须与模型输出类别一一对应。
 *
 * 模型来源：Rishit-dagli/Greenathon-Plant-AI v0.1.0（Apache 2.0）
 * 输入：224x224x3，像素归一化到 [0,1]；输出：38 维 logits，取 argmax 得类别索引。
 */
public final class DiseaseKnowledge {

    /** 免责声明（结果页展示 + TTS 播报） */
    public static final String DISCLAIMER =
            "AI识别结果仅供参考，具体病害防治请咨询当地农技人员或农业专家。";

    /** 英文标签 -> 中文病害名 */
    private static final Map<String, String> NAME_MAP = new HashMap<>();
    /** 英文标签 -> 防治建议 */
    private static final Map<String, String> SUGGESTION_MAP = new HashMap<>();

    private static void put(String en, String zh, String suggestion) {
        NAME_MAP.put(en, zh);
        SUGGESTION_MAP.put(en, suggestion);
    }

    static {
        put("Apple___Apple_scab", "苹果黑星病",
                "清除落叶落果并集中销毁，发病初期喷施代森锰锌或苯醚甲环唑，加强果园通风透光。");
        put("Apple___Black_rot", "苹果黑腐病",
                "剪除病枝病果并销毁，喷施甲基硫菌灵或多抗霉素，加强树势管理。");
        put("Apple___Cedar_apple_rust", "苹果锈病",
                "清除果园附近桧柏等转主寄主，春季萌芽后喷施三唑酮或戊唑醇。");
        put("Apple___healthy", "苹果长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Blueberry___healthy", "蓝莓长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Cherry_(including_sour)___Powdery_mildew", "樱桃白粉病",
                "加强通风降湿，喷施醚菌酯或硫制剂，剪除重病梢叶。");
        put("Cherry_(including_sour)___healthy", "樱桃长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot", "玉米灰斑病",
                "发病初期喷施苯醚甲环唑或吡唑醚菌酯，收获后清除病残体，合理密植。");
        put("Corn_(maize)___Common_rust_", "玉米普通锈病",
                "喷施三唑酮或嘧菌酯，选用抗病品种，合理密植。");
        put("Corn_(maize)___Northern_Leaf_Blight", "玉米大斑病",
                "发病初期喷施多菌灵或吡唑醚菌酯，及时排水，轮作倒茬。");
        put("Corn_(maize)___healthy", "玉米长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Grape___Black_rot", "葡萄黑腐病",
                "清除病果病穗，花后喷施苯醚甲环唑或代森锰锌，雨季注意排湿。");
        put("Grape___Esca_(Black_Measles)", "葡萄黑麻疹病",
                "剪除病蔓并销毁，伤口涂抹杀菌剂保护，加强水肥均衡管理。");
        put("Grape___Leaf_blight_(Isariopsis_Leaf_Spot)", "葡萄褐斑病",
                "喷施多菌灵或代森锰锌，清除落叶，改善通风透光。");
        put("Grape___healthy", "葡萄长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Orange___Haunglongbing_(Citrus_greening)", "柑橘黄龙病",
                "目前无有效药剂，发现病株应立即挖除销毁，重点防治柑橘木虱，选用无病苗木。");
        put("Peach___Bacterial_spot", "桃细菌性穿孔病",
                "剪除病枝，萌芽前喷石硫合剂，生长期喷施农用链霉素或噻唑锌，注意排水。");
        put("Peach___healthy", "桃长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Pepper_bell___Bacterial_spot", "辣椒细菌性叶斑病",
                "喷施农用链霉素或铜制剂，避免带露水作业，实行轮作。");
        put("Pepper_bell___healthy", "辣椒长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Potato___Early_blight", "马铃薯早疫病",
                "喷施代森锰锌或苯醚甲环唑，及时清除病叶，避免田间过湿。");
        put("Potato___Late_blight", "马铃薯晚疫病",
                "发现中心病株立即喷施烯酰吗啉或氟菌霜霉威，雨前雨后重点防控，及时排水。");
        put("Potato___healthy", "马铃薯长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Raspberry___healthy", "树莓长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Soybean___healthy", "大豆长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Squash___Powdery_mildew", "南瓜白粉病",
                "喷施醚菌酯或硫磺悬浮剂，加强通风，避免偏施氮肥。");
        put("Strawberry___Leaf_scorch", "草莓叶焦病",
                "控制种植密度，喷施嘧菌酯或苯醚甲环唑，及时摘除病叶。");
        put("Strawberry___healthy", "草莓长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
        put("Tomato___Bacterial_spot", "番茄细菌性斑点病",
                "喷施噻唑锌或农用链霉素，避免湿度过高，播种前进行种子消毒。");
        put("Tomato___Early_blight", "番茄早疫病",
                "喷施代森锰锌或苯醚甲环唑，摘除病叶，加强通风降湿。");
        put("Tomato___Late_blight", "番茄晚疫病",
                "立即喷施烯酰吗啉等药剂，控制棚内湿度，及时通风并清除病株。");
        put("Tomato___Leaf_Mold", "番茄叶霉病",
                "加强通风降湿，喷施氟菌肟菌酯或春雷霉素，及时摘除病叶。");
        put("Tomato___Septoria_leaf_spot", "番茄斑枯病",
                "喷施多菌灵或百菌清，避免叶面长时间结露，清除病残体。");
        put("Tomato___Spider_mites Two-spotted_spider_mite", "番茄红蜘蛛（二斑叶螨）",
                "喷施阿维菌素或联苯肼酯，注意叶背施药，防止干燥环境下蔓延。");
        put("Tomato___Target_Spot", "番茄靶斑病",
                "喷施唑醚代森联或氟菌肟菌酯，控制湿度，及时清除病叶。");
        put("Tomato___Tomato_Yellow_Leaf_Curl_Virus", "番茄黄化曲叶病毒病",
                "重点防治烟粉虱，及时拔除病株，选用抗病品种。");
        put("Tomato___Tomato_mosaic_virus", "番茄花叶病毒病",
                "播前种子消毒，操作时避免交叉感染，防治传毒介体，拔除病株。");
        put("Tomato___healthy", "番茄长势健康",
                "长势良好，继续做好水肥管理和病虫害预防。");
    }

    private DiseaseKnowledge() {
    }

    /** 取中文病害名；未知标签返回 null，由调用方兜底 */
    public static String getChineseName(String enLabel) {
        return NAME_MAP.get(enLabel);
    }

    /** 取防治建议；未知标签返回 null，由调用方兜底 */
    public static String getSuggestion(String enLabel) {
        return SUGGESTION_MAP.get(enLabel);
    }

    /** TTS 播报文案：检测到XX病，建议XX（健康则播长势良好） */
    public static String buildTtsText(String enLabel) {
        String name = getChineseName(enLabel);
        String suggestion = getSuggestion(enLabel);
        if (name == null || suggestion == null) {
            return null;
        }
        if (enLabel.endsWith("healthy")) {
            return "检测结果：" + name + "。" + suggestion;
        }
        return "检测到" + name + "，建议：" + suggestion + "。" + DISCLAIMER;
    }
}
