package com.x;
@FeignClient(name = "billing", url = "http://billing")
@RequestMapping("/api")
public interface Billing {
    @GetMapping("/invoices") String invoices();
}
