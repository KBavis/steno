package dev.steno.resolver;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * The JVM helper for Java symbol resolution (D69, docs/how-it-works/ingestion/resolvers.md §5.2).
 *
 * <pre>
 * steno-resolver --root REPO --files FILES.txt --out OUT.json [--jars JARS.txt]
 * </pre>
 *
 * <p>FILES.txt lists the Java files to read, one path per line (the engine decides which are
 * first-party and not tests). JARS.txt lists dependency JARs; it starts empty. The whole
 * repository is resolved in one pass and written to OUT.json, which the Java resolver on the
 * Python side reads instead of asking one question at a time.
 */
public final class Main {
    private Main() {}

    public static void main(String[] args) throws IOException {
        Path root = null;
        Path files = null;
        Path out = null;
        Path jars = null;
        for (int i = 0; i + 1 < args.length; i += 2) {
            switch (args[i]) {
                case "--root" -> root = Path.of(args[i + 1]);
                case "--files" -> files = Path.of(args[i + 1]);
                case "--out" -> out = Path.of(args[i + 1]);
                case "--jars" -> jars = Path.of(args[i + 1]);
                default -> usage();
            }
        }
        if (root == null || files == null || out == null) {
            usage();
            return;
        }
        root = root.toAbsolutePath().normalize();
        List<Path> sources = new ArrayList<>();
        for (String line : Files.readAllLines(files, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                Path p = Path.of(line.strip());
                sources.add(p.isAbsolute() ? p : root.resolve(p));
            }
        }
        List<Path> jarList = new ArrayList<>();
        if (jars != null) {
            for (String line : Files.readAllLines(jars, StandardCharsets.UTF_8)) {
                if (!line.isBlank()) {
                    jarList.add(Path.of(line.strip()));
                }
            }
        }
        Map<String, Object> result = new Analyzer(root, jarList).run(sources);
        try (BufferedWriter w = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
            Json.write(result, w);
        }
    }

    private static void usage() {
        System.err.println("usage: steno-resolver --root REPO --files FILES.txt --out OUT.json [--jars JARS.txt]");
        System.exit(2);
    }
}
