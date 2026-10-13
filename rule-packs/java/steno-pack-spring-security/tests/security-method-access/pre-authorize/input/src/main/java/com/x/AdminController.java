package com.x;

import org.springframework.security.access.prepost.PreAuthorize;

public class AdminController {
    @PreAuthorize("hasRole('ADMIN')")
    public void purge() {}
}
