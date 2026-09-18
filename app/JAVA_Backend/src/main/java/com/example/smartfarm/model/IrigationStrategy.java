package com.example.smartfarm.model;

import lombok.Data;

@Data
public class IrigationStrategy {
    private boolean shouldIrrigate;
    private int irrigationAmount;
    private String reason;
    private int soilHumidity;
    private int rainForecast;
    
    public static IrigationStrategy calculate(int soilHumidity, int rain24h) {
        IrigationStrategy strategy = new IrigationStrategy();
        strategy.setSoilHumidity(soilHumidity);
        strategy.setRainForecast(rain24h);
        
        if (soilHumidity >= 30) {
            strategy.setShouldIrrigate(false);
            strategy.setIrrigationAmount(0);
            strategy.setReason("土壤湿度充足，无需灌溉");
        } else if (rain24h >= 20) {
            strategy.setShouldIrrigate(false);
            strategy.setIrrigationAmount(0);
            strategy.setReason("未来24小时有中到大雨，暂停灌溉");
        } else if (rain24h >= 5) {
            strategy.setShouldIrrigate(true);
            strategy.setIrrigationAmount(50);
            strategy.setReason("未来24小时有小雨，减少50%灌溉量");
        } else if (soilHumidity < 10) {
            strategy.setShouldIrrigate(true);
            strategy.setIrrigationAmount(100);
            strategy.setReason("土壤湿度低，无降雨预报，正常灌溉");
        } else if (soilHumidity < 20) {
            strategy.setShouldIrrigate(true);
            strategy.setIrrigationAmount(70);
            strategy.setReason("土壤湿度偏低，无降雨预报，70%灌溉量");
        } else {
            strategy.setShouldIrrigate(false);
            strategy.setIrrigationAmount(0);
            strategy.setReason("土壤湿度适中，无需灌溉");
        }
        
        return strategy;
    }
}