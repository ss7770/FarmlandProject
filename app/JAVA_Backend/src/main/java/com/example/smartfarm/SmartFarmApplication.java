package com.example.smartfarm;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class SmartFarmApplication {
    public static void main(String[] args) {
        SpringApplication.run(SmartFarmApplication.class, args);
    }
}