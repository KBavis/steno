package com.x;

@FeignClient("users-service")
@RequestMapping("/api")
public interface UsersClient {
    @GetMapping("/users/{id}")
    String get(@PathVariable("id") String id);
}
