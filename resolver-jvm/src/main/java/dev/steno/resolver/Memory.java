package dev.steno.resolver;

import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** How much memory this run needed, so a repository outgrowing its heap shows up before it fails (D127). */
final class Memory {
    private static final long MB = 1024 * 1024;

    private Memory() {}

    private static long liveMax = 0;

    /** Note how much of the heap survived the last collection (called between batches). */
    static void sample() {
        for (MemoryPoolMXBean pool : ManagementFactory.getMemoryPoolMXBeans()) {
            if (pool.getType() == MemoryType.HEAP && pool.getCollectionUsage() != null && isOld(pool)) {
                liveMax = Math.max(liveMax, pool.getCollectionUsage().getUsed());
            }
        }
    }

    /**
     * The most heap that survived a collection (what this repository really needs: the JVM
     * fills its heap before collecting, so plain usage always nears the limit), the heap limit,
     * and the process's peak resident memory.
     */
    static Map<String, Object> peaks() {
        sample();
        Map<String, Object> out = new LinkedHashMap<>();
        // No collection reached the old generation (a small repository): what's in use now
        long live = liveMax > 0 ? liveMax : ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed();
        out.put("heap_live_peak_mb", live / MB);
        out.put("heap_limit_mb", Runtime.getRuntime().maxMemory() / MB);
        Long rss = residentPeakKb();
        if (rss != null) {
            out.put("rss_peak_mb", rss / 1024);
        }
        return out;
    }

    private static boolean isOld(MemoryPoolMXBean pool) {
        return pool.getName().contains("Old") || pool.getName().contains("Tenured");
    }

    /** VmHWM from /proc/self/status (Linux); null elsewhere. */
    private static Long residentPeakKb() {
        try {
            for (String line : Files.readAllLines(Path.of("/proc/self/status"))) {
                if (line.startsWith("VmHWM:")) {
                    return Long.parseLong(line.replaceAll("[^0-9]", ""));
                }
            }
        } catch (IOException | RuntimeException e) {
            return null;
        }
        return null;
    }
}
