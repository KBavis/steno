package com.x;

import org.springframework.http.HttpMethod;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;

public class UsersGateway {
    private static final String BASE = "http://users.internal";
    private final RestTemplate rest;
    private final WebClient web;
    private final RestClient client;
    private final java.util.Map<String, String> cache;

    public UsersGateway(RestTemplate rest, WebClient web, RestClient client, java.util.Map<String, String> cache) {
        this.rest = rest; this.web = web; this.client = client; this.cache = cache;
    }

    public String user(String id) {
        cache.put("last", id);
        return rest.getForObject(BASE + "/users/{id}", String.class, id);
    }

    public void remove(String id) {
        rest.delete(BASE + "/users/" + id);
    }

    public String rename(String id) {
        return rest.exchange(BASE + "/users/{id}/name", HttpMethod.PATCH, null, String.class, id).getBody();
    }

    public void charge(String id) {
        web.post().uri("/charge/{id}", id).retrieve();
        client.get().uri(BASE + "/health").retrieve();
    }
}
