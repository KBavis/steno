package com.x;
public class Publisher {
    static class Mailer { void send(String to, String body) { } }
    private final Mailer mailer = new Mailer();
    void publish(String event) { mailer.send("events-out-0", event); }
}
