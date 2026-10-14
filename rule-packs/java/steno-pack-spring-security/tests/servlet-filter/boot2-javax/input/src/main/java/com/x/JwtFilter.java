package com.x;

import javax.servlet.FilterChain;
import javax.servlet.http.*;
import org.springframework.web.filter.OncePerRequestFilter;

// Spring Boot 2: the servlet API is javax.servlet
public class JwtFilter extends OncePerRequestFilter {
    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) {
        check(req);
    }

    private void check(HttpServletRequest req) {}
}
