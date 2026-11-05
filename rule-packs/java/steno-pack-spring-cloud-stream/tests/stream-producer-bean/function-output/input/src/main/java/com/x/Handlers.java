package com.x;
import java.util.function.Function;
import org.springframework.context.annotation.Bean;
public class Handlers {
    @Bean
    public Function<String, String> enrich() { return s -> s; }
}
