package com.x;
@FeignClient(name = "billing", url = "http://billing")
public interface Billing {
    @GetMapping("/invoices") String invoices();
}
