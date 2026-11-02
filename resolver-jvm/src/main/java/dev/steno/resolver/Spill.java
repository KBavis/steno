package dev.steno.resolver;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * The helper's largest outputs, every expression a rule may capture, every function's calls,
 * and every function's description, written to disk as each batch finishes instead of held
 * until the end (D127): on a large repository they're a large share of the heap. The output
 * file reads the same as before, unless the parts are kept: then expressions and calls are JSON
 * Lines in their own files (an expression per line; a function's symbol and its calls per line),
 * for the reader to index on disk. Functions always go into the output file.
 */
final class Spill implements AutoCloseable {
    private final Path exprsFile;
    private final Path callsFile;
    private final Path functionsFile;
    private final BufferedWriter exprs;
    private final BufferedWriter calls;
    private final BufferedWriter functions;
    private final boolean keep;
    private int exprCount;
    private int callCount;
    private int functionCount;

    Spill(Path dir) throws IOException {
        this(dir, false);
    }

    /** With `keep`, the parts are `exprs.jsonl` and `calls.jsonl` in `dir`, left there. */
    Spill(Path dir, boolean keep) throws IOException {
        this.keep = keep;
        exprsFile = keep ? dir.resolve("exprs.jsonl") : Files.createTempFile(dir, "exprs", ".part");
        callsFile = keep ? dir.resolve("calls.jsonl") : Files.createTempFile(dir, "calls", ".part");
        functionsFile = Files.createTempFile(dir, "functions", ".part");
        exprs = Files.newBufferedWriter(exprsFile, StandardCharsets.UTF_8);
        calls = Files.newBufferedWriter(callsFile, StandardCharsets.UTF_8);
        functions = Files.newBufferedWriter(functionsFile, StandardCharsets.UTF_8);
    }

    void function(Map<String, Object> f) throws IOException {
        if (functionCount++ > 0) {
            functions.write(',');
        }
        Json.write(f, functions);
    }

    void expr(Map<String, Object> e) throws IOException {
        if (exprCount++ > 0) {
            exprs.write(keep ? '\n' : ',');
        }
        Json.write(e, exprs);
    }

    void calls(String symbol, List<Map<String, Object>> list) throws IOException {
        if (callCount++ > 0) {
            calls.write(keep ? '\n' : ',');
        }
        if (keep) {
            calls.write('[');
            Json.write(symbol, calls);
            calls.write(',');
            Json.write(list, calls);
            calls.write(']');
            return;
        }
        Json.write(symbol, calls);
        calls.write(':');
        Json.write(list, calls);
    }

    int expressions() {
        return exprCount;
    }

    /** The output file: the spilled parts (unless they're kept), then everything else. */
    void writeWith(Map<String, Object> rest, Writer out) throws IOException {
        exprs.close();
        calls.close();
        functions.close();
        out.write("{\"functions\":[");
        copy(functionsFile, out);
        out.write(']');
        if (!keep) {
            out.write(",\"exprs\":[");
            copy(exprsFile, out);
            out.write("],\"calls\":{");
            copy(callsFile, out);
            out.write('}');
        }
        for (Map.Entry<String, Object> e : rest.entrySet()) {
            if (e.getValue() == null) {
                continue;
            }
            out.write(',');
            Json.write(e.getKey(), out);
            out.write(':');
            Json.write(e.getValue(), out);
        }
        out.write('}');
    }

    private static void copy(Path file, Writer out) throws IOException {
        try (var in = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            in.transferTo(out);
        }
    }

    @Override
    public void close() throws IOException {
        exprs.close();
        calls.close();
        functions.close();
        Files.deleteIfExists(functionsFile);
        if (!keep) {
            Files.deleteIfExists(exprsFile);
            Files.deleteIfExists(callsFile);
        }
    }
}
