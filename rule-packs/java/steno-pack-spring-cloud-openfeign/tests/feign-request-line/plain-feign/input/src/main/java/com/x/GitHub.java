package com.x;

import feign.RequestLine;

public interface GitHub {
    @RequestLine("GET /repos/{owner}/{repo}/contributors")
    String contributors(@Param("owner") String owner, @Param("repo") String repo);
}
