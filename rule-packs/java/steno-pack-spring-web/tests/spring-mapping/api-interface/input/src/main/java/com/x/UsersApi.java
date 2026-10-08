package com.x;

import org.springframework.web.bind.annotation.*;

@RequestMapping("/users")
public interface UsersApi {
    @GetMapping("/{id}")
    String getUser(@PathVariable("id") String id);
}
