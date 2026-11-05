package com.x;
import org.springframework.cloud.stream.function.StreamBridge;
public class Publisher {
    private final StreamBridge bridge;
    Publisher(StreamBridge bridge) { this.bridge = bridge; }
    void publish(String event) { bridge.send("events-out-0", event); }
}
