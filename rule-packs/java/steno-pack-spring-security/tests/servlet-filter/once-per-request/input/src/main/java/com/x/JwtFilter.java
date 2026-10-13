package com.x;

import jakarta.servlet.FilterChain;
import jakarta.servlet.http.*;
import org.springframework.web.filter.OncePerRequestFilter;

public class JwtFilter extends OncePerRequestFilter {
    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) {
        check(req);
    }

    private void check(HttpServletRequest req) {}
}
