package com.x;

import org.springframework.context.annotation.Bean;

public class AuthConfig {
    @Bean
    public UserLookup userLookup() {
        return name -> null;
    }

    public UserLookup notABean() { return null; }
}
