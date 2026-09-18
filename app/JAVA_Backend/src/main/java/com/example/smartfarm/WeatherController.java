package com.example.smartfarm;

import com.example.smartfarm.model.IrigationStrategy;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.beans.factory.annotation.Value;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.HashMap;
import java.util.Map;
import java.util.zip.GZIPInputStream;

@RestController
@RequestMapping("/api")
public class WeatherController {
    @Value("${weather.api-key}")
    private String API_KEY;

    @Value("${weather.city-code}")
    private String LOCATION;

    @Value("${weather.base-url}")
    private String QWEATHER_URL;

    @Value("${sensor.db-path}")
    private String dbPath;
   
    @GetMapping("/weather")
    public Map<String, Object> getWeather() throws Exception {
        // ---------- 1. 获取实时天气数据 ----------
        String urlStr = String.format("%s?location=%s&key=%s", QWEATHER_URL, LOCATION, API_KEY);
        URL url = new URL(urlStr);
        HttpURLConnection httpConn = (HttpURLConnection) url.openConnection();
        httpConn.setRequestMethod("GET");
        httpConn.setRequestProperty("Accept", "application/json");
        httpConn.setRequestProperty("Accept-Encoding", "gzip, deflate");

        java.io.InputStream is = httpConn.getInputStream();
        if ("gzip".equals(httpConn.getContentEncoding())) {
            is = new GZIPInputStream(is);
        }

        BufferedReader reader = new BufferedReader(new InputStreamReader(is, "UTF-8"));
        StringBuilder response = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            response.append(line);
        }
        reader.close();
        httpConn.disconnect();

        String rawJson = response.toString();

        // ---------- 2. 解析 JSON ----------
        com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        Map<String, Object> responseMap = mapper.readValue(rawJson, Map.class);
        Map<String, Object> now = (Map<String, Object>) responseMap.get("now");

        int temperature = Integer.parseInt((String) now.get("temp"));
        String weatherText = (String) now.get("text");
        int humidity = Integer.parseInt((String) now.get("humidity"));

        // ---------- 3. 从 SQLite 读取土壤湿度 ----------
        int soilHumidity = 45; // 默认值
        String dbPath = "jdbc:sqlite:D:/Twilight/FarmlandProject/project1.0/app/Python_Backend/sensor.db";

        try (Connection dbConn = DriverManager.getConnection(dbPath);
             Statement stmt = dbConn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT soil FROM data ORDER BY id DESC LIMIT 1")) {

            if (rs.next()) {
                soilHumidity = rs.getInt("soil");
            }

        } catch (Exception e) {
            System.out.println("读取数据库失败，使用默认值 45：" + e.getMessage());
        }

        // ---------- 4. 计算灌溉策略 ----------
        int rain24h = 0; // 未来24小时降雨量，可后续接入预报接口
        IrigationStrategy strategy = IrigationStrategy.calculate(soilHumidity, rain24h);

        // ---------- 5. 组装返回结果 ----------
        Map<String, Object> result = new HashMap<>();
        result.put("temperature", temperature);
        result.put("weather", weatherText);
        result.put("rain24h", rain24h);
        result.put("humidity", humidity);
        result.put("soilHumidity", soilHumidity);
        result.put("shouldIrrigate", strategy.isShouldIrrigate());
        result.put("irrigationAmount", strategy.getIrrigationAmount());
        result.put("reason", strategy.getReason());

        return result;
    }
}