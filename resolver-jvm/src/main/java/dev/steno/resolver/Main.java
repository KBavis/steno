package dev.steno.resolver;

import com.github.javaparser.ParserConfiguration;

/**
 * Entry point for the resolver helper.
 *
 * <p>Placeholder: the protocol between the Python worker and this process (how a
 * workspace, its source roots, and dependency JARs are passed in, and how resolved
 * call sites come back) is not designed yet.
 */
public final class Main {
    private Main() {}

    public static void main(String[] args) {
        ParserConfiguration config = new ParserConfiguration();
        System.out.println("{\"status\":\"ok\",\"language_level\":\"" + config.getLanguageLevel() + "\"}");
    }
}
