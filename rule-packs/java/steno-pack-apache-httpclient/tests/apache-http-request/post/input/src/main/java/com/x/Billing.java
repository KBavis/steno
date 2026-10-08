package com.x;

import org.apache.hc.client5.http.classic.methods.HttpPost;

public class Billing {
    public void charge(String id) {
        HttpPost post = new HttpPost("https://billing.internal/charges/" + id);
    }
}
