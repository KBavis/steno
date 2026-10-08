package com.x;

import java.net.URI;
import java.net.http.HttpRequest;

public class Pinger {
    public void ping() {
        HttpRequest r = HttpRequest.newBuilder().uri(URI.create("https://status.internal/ping")).GET().build();
    }

    public void report(HttpRequest.BodyPublisher body) {
        HttpRequest r = HttpRequest.newBuilder(URI.create("https://status.internal/report")).POST(body).build();
    }
}
