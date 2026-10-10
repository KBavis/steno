// The JVM helper for symbol and DI resolution (docs/how-it-works/ingestion/resolvers.md, D13).
// JavaParser's symbol solver resolves a repository in one pass from source and the JDK
// (dependency JARs optional, D69), without a build. Run by resolvers/java.py.
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
