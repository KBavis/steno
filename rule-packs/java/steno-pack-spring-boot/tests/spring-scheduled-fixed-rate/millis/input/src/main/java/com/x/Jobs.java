package com.x;

import org.springframework.scheduling.annotation.Scheduled;

public class Jobs {
    @Scheduled(cron = "0 55 23 L * ?")
    public void monthly() {}

    @Scheduled(fixedRate = 60000)
    public void poll() {}

    @Scheduled(fixedDelayString = "${jobs.cleanup.delay:30000}")
    public void cleanup() {}
}
