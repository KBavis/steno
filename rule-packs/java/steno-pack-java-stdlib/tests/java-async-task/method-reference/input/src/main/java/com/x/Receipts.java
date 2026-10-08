package com.x;

import java.util.concurrent.CompletableFuture;

public class Receipts {
    public void checkout() {
        CompletableFuture.runAsync(this::sendReceipt);
    }

    private void sendReceipt() {}
}
