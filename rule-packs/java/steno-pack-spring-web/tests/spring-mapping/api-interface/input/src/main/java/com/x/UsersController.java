package com.x;

@RestController
public class UsersController implements UsersApi {
    @Override
    public String getUser(String id) { return id; }
}
