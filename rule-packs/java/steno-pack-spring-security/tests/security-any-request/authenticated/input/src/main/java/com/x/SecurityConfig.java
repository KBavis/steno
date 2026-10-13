package com.x;

import org.springframework.http.HttpMethod;

public class SecurityConfig {
    public Object chain(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests((authz) -> authz
                .requestMatchers("/auth/login", "/auth/register").permitAll()
                .requestMatchers(HttpMethod.POST, "/admin/jobs").hasAuthority("ADMIN")
                .anyRequest().authenticated());
        return http.build();
    }
}
