package com.x;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(name = "plaidClient", url = "${plaid.api.baseUrl}")
public interface PlaidClient {
    @PostMapping("/link/token/create")
    String createLinkToken(@RequestBody String request);

    @GetMapping
    String root();
}
