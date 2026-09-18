package com.example.smartfarm;

public class SensorData {
    private int temperature;
    private int humidity;
    private int lightLux;
    private int soilHumidity;
    private int waterLevel;

    public SensorData() {
        this.temperature = 0;
        this.humidity = 0;
        this.lightLux = 0;
        this.soilHumidity = 0;
        this.waterLevel = 0;
    }

    public SensorData(int temperature, int humidity, int lightLux, int soilHumidity, int waterLevel) {
        this.temperature = temperature;
        this.humidity = humidity;
        this.lightLux = lightLux;
        this.soilHumidity = soilHumidity;
        this.waterLevel = waterLevel;
    }

    public static SensorData parseFrom(String data) {
        SensorData sensorData = new SensorData();
        if (data == null || data.isEmpty()) {
            return sensorData;
        }

        String[] parts = data.split(",");
        for (String part : parts) {
            String[] keyValue = part.split(":");
            if (keyValue.length == 2) {
                String key = keyValue[0].trim();
                String value = keyValue[1].trim();
                try {
                    switch (key) {
                        case "TEMP":
                            sensorData.temperature = Integer.parseInt(value);
                            break;
                        case "HUMI":
                            sensorData.humidity = Integer.parseInt(value);
                            break;
                        case "LIGHT":
                            sensorData.lightLux = Integer.parseInt(value);
                            break;
                        case "SOIL":
                            sensorData.soilHumidity = Integer.parseInt(value);
                            break;
                        case "WATER":
                            sensorData.waterLevel = Integer.parseInt(value);
                            break;
                    }
                } catch (NumberFormatException e) {
                    e.printStackTrace();
                }
            }
        }
        return sensorData;
    }

    public int getTemperature() {
        return temperature;
    }

    public void setTemperature(int temperature) {
        this.temperature = temperature;
    }

    public int getHumidity() {
        return humidity;
    }

    public void setHumidity(int humidity) {
        this.humidity = humidity;
    }

    public int getLightLux() {
        return lightLux;
    }

    public void setLightLux(int lightLux) {
        this.lightLux = lightLux;
    }

    public int getSoilHumidity() {
        return soilHumidity;
    }

    public void setSoilHumidity(int soilHumidity) {
        this.soilHumidity = soilHumidity;
    }

    public int getWaterLevel() {
        return waterLevel;
    }

    public void setWaterLevel(int waterLevel) {
        this.waterLevel = waterLevel;
    }

    @Override
    public String toString() {
        return "SensorData{" +
                "temperature=" + temperature +
                ", humidity=" + humidity +
                ", lightLux=" + lightLux +
                ", soilHumidity=" + soilHumidity +
                ", waterLevel=" + waterLevel +
                '}';
    }
}