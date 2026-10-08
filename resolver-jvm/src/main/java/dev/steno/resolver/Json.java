package dev.steno.resolver;

import java.io.IOException;
import java.io.Writer;
import java.util.Collection;
import java.util.Map;

/** A small JSON writer for the helper's output: maps, lists, strings, numbers, booleans. */
final class Json {
    private Json() {}

    static void write(Object value, Writer out) throws IOException {
        if (value == null) {
            out.write("null");
        } else if (value instanceof String s) {
            string(s, out);
        } else if (value instanceof Number || value instanceof Boolean) {
            out.write(value.toString());
        } else if (value instanceof Map<?, ?> map) {
            out.write('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (e.getValue() == null) {
                    continue;
                }
                if (!first) {
                    out.write(',');
                }
                first = false;
                string(String.valueOf(e.getKey()), out);
                out.write(':');
                write(e.getValue(), out);
            }
            out.write('}');
        } else if (value instanceof Collection<?> list) {
            out.write('[');
            boolean first = true;
            for (Object item : list) {
                if (!first) {
                    out.write(',');
                }
                first = false;
                write(item, out);
            }
            out.write(']');
        } else {
            string(value.toString(), out);
        }
    }

    private static void string(String s, Writer out) throws IOException {
        out.write('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> out.write("\\\"");
                case '\\' -> out.write("\\\\");
                case '\n' -> out.write("\\n");
                case '\r' -> out.write("\\r");
                case '\t' -> out.write("\\t");
                default -> {
                    if (c < 0x20) {
                        out.write(String.format("\\u%04x", (int) c));
                    } else {
                        out.write(c);
                    }
                }
            }
        }
        out.write('"');
    }
}
