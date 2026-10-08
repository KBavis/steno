package com.x;

import okhttp3.*;

public class SuggestionClient {
    private static final String BASE = "http://suggestion-engine:8000/";

    public void suggest(RequestBody body) {
        Request request = new Request.Builder().url(BASE + "suggestion").post(body).build();
    }

    public void health() {
        Request request = new Request.Builder().url(BASE + "health").build();
    }
}
