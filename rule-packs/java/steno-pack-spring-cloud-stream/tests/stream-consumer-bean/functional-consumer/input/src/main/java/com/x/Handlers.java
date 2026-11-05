package com.x;
import java.util.function.Consumer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
@Configuration
public class Handlers {
    @Bean
    public Consumer<String> onOrder() { return s -> { }; }
}
