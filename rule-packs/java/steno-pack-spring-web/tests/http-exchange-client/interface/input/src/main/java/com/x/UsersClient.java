package com.x;

import org.springframework.web.service.annotation.*;

@HttpExchange(url = "http://users.internal")
public interface UsersClient {
    @GetExchange("/users/{id}")
    String get(String id);

    @PostExchange(url = "/users")
    String create(String body);
}
