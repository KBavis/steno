package com.x;

import org.springframework.kafka.annotation.*;

@KafkaListener(topics = "notifications")
public class Fanout {
    @KafkaHandler
    public void on(String message) {}
}
