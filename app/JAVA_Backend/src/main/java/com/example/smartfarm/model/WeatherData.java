package com.example.smartfarm.model;

import lombok.Data;

@Data
public class WeatherData {
    private int rain12h;
    private int rain24h;
    private int rain48h;
    private int temperature;
    private int humidity;
    private String weather;
    private String updateTime;
    
    public String toStm32Format() {
        return String.format("WEATHER:RAIN:%d/24,TEMP:%d,HUMI:%d", 
            rain24h, temperature, humidity);
    }
}