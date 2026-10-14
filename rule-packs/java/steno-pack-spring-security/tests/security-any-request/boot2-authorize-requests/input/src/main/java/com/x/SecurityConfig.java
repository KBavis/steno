package com.x;

import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;

// Spring Boot 2 / Security 5: a WebSecurityConfigurerAdapter with authorizeRequests() and antMatchers
public class SecurityConfig extends WebSecurityConfigurerAdapter {
    @Override
    protected void configure(HttpSecurity http) throws Exception {
        http.csrf().disable()
                .authorizeRequests()
                .antMatchers("/auth/login", "/auth/register").permitAll()
                .antMatchers(HttpMethod.POST, "/admin/jobs").hasAuthority("ADMIN")
                .anyRequest().authenticated();
    }
}
