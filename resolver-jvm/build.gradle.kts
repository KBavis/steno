// The JVM helper for symbol and DI resolution (docs/ingestion.md §5, D13).
// JavaParser's symbol solver resolves calls from source plus the dependency JARs,
// without a full build. Called from the Python ingestion worker.
plugins {
    application
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("com.github.javaparser:javaparser-symbol-solver-core:3.27.0")
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

application {
    mainClass = "dev.steno.resolver.Main"
}
