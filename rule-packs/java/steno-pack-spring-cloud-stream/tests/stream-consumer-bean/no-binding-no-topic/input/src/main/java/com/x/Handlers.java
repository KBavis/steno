package com.x;
import java.util.function.Consumer;
import org.springframework.context.annotation.Bean;
public class Handlers {
    @Bean
    public Consumer<String> unbound() { return s -> { }; }
    public Consumer<String> notABean() { return s -> { }; }
}
