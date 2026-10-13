"""The Java version a repository builds with, read from its build files: the JVM helper
parses at that level."""

import pytest

from steno.resolvers.java import java_version


@pytest.mark.parametrize(
    ("name", "text", "version"),
    [
        ("build.gradle", "sourceCompatibility = '17'", "17"),
        ("build.gradle", "sourceCompatibility = JavaVersion.VERSION_1_8", "8"),
        ("build.gradle.kts", "toolchain { languageVersion = JavaLanguageVersion.of(21) }", "21"),
        ("build.gradle.kts", "options.release.set(11)", "11"),
        ("pom.xml", "<maven.compiler.source>1.8</maven.compiler.source>", "8"),
        ("pom.xml", "<properties><java.version>17</java.version></properties>", "17"),
    ],
)
def test_declared_versions(tmp_path, name, text, version):
    (tmp_path / name).write_text(text)
    assert java_version(tmp_path) == version


def test_the_highest_module_wins_and_none_without_a_build(tmp_path):
    assert java_version(tmp_path) is None
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "pom.xml").write_text("<java.version>11</java.version>")
    (tmp_path / "b" / "pom.xml").write_text("<java.version>17</java.version>")
    assert java_version(tmp_path) == "17"
