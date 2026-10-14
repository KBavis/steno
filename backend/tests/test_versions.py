"""Library versions from build files, and packs checked against the versions they're tested
on: a library on another version, or one no build file states, is a coverage item."""

from pathlib import Path
from textwrap import dedent

import pytest

from steno.coverage.collect import _versions
from steno.rule_packs.packs import Pack, declared_dependencies
from steno.rule_packs.versions import dependencies, in_range, parse_version, versions_of

FILES = {
    # Gradle: the Boot plugin, a coordinate with a variable, and a script applied from another
    "app/build.gradle": """
        plugins { id 'org.springframework.boot' version "${springBootVersion}" }
        apply from: "$rootDir/dependencies.gradle"
        dependencies {
            implementation libraries.web
            implementation "com.squareup.okhttp3:okhttp:${versions.okhttp}"
            implementation group: 'org.apache.kafka', name: 'kafka-clients', version: '3.6.1'
        }
    """,
    "dependencies.gradle": """
        ext.versions = [okhttp: '4.12.0', feign: '12.5']
        ext.libraries = [
            web: "org.springframework.boot:spring-boot-starter-web",
            feign: "io.github.openfeign:feign-core:${versions.feign}",
        ]
    """,
    "gradle.properties": "springBootVersion=2.7.18\nlombokVersion=1.18.30\n",
    # Kotlin, and a property looked up by name; an environment variable can't be known
    "lib/build.gradle.kts": """
        val lombokVersion: String by project
        dependencies {
            compileOnly("org.projectlombok:lombok:${property("lombokVersion")}")
            implementation("com.google.guava:guava:${System.getenv("GUAVA_VERSION")}")
        }
    """,
    # A version catalog
    "gradle/libs.versions.toml": """
        [versions]
        grpc = "1.60.0"
        [libraries]
        grpc-stub = { module = "io.grpc:grpc-stub", version.ref = "grpc" }
        jackson = "com.fasterxml.jackson.core:jackson-databind:2.16.1"
    """,
    # Maven: the parent POM and a property
    "svc/pom.xml": """
        <project>
          <parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>3.2.2</version>
          </parent>
          <properties><httpclient.version>5.3</httpclient.version></properties>
          <dependencies>
            <dependency>
              <groupId>org.apache.httpcomponents.client5</groupId>
              <artifactId>httpclient5</artifactId>
              <version>${httpclient.version}</version>
            </dependency>
          </dependencies>
        </project>
    """,
    # Python: a pin, a range (not a version), and a lock file
    "py/requirements.txt": "fastapi==0.115.12\nhttpx>=0.27\n",
    "py/uv.lock": """
        [[package]]
        name = "sqlalchemy"
        version = "2.0.40"
    """,
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("versions")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(dedent(text).lstrip())
    return root


def _found(repo: Path, key: str) -> set[tuple[str | None, str]]:
    return {(d.version, d.file) for d in versions_of(dependencies(repo), key)}


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        # The plugin's `${springBootVersion}` comes from gradle.properties; the parent POM's
        # version is a Boot version too
        (
            "org.springframework.boot",
            {("2.7.18", "app/build.gradle"), ("3.2.2", "svc/pom.xml")},
        ),
        ("com.squareup.okhttp3:okhttp", {("4.12.0", "app/build.gradle")}),
        ("io.github.openfeign:feign-core", {("12.5", "dependencies.gradle")}),
        ("org.apache.kafka:kafka-clients", {("3.6.1", "app/build.gradle")}),
        ("io.grpc", {("1.60.0", "gradle/libs.versions.toml")}),
        ("com.fasterxml.jackson.core:jackson-databind", {("2.16.1", "gradle/libs.versions.toml")}),
        ("org.apache.httpcomponents.client5:httpclient5", {("5.3", "svc/pom.xml")}),
        ("fastapi", {("0.115.12", "py/requirements.txt")}),
        ("sqlalchemy", {("2.0.40", "py/uv.lock")}),
        ("httpx", set()),  # `>=0.27` is a range, not the version that runs
        ("org.projectlombok:lombok", {("1.18.30", "lib/build.gradle.kts")}),
        ("com.google.guava:guava", set()),  # set by the environment at build time
    ],
)
def test_versions_from_build_files(repo: Path, key: str, expected: set) -> None:
    assert _found(repo, key) == expected


def test_scripts_and_catalogs_enable_packs(repo: Path) -> None:
    deps = declared_dependencies(repo)
    # Declared only in dependencies.gradle and the version catalog
    assert "io-github-openfeign:feign-core" in deps
    assert "io-grpc:grpc-stub" in deps
    # Lock files list what other libraries pull in: not a reason to enable a pack
    assert "sqlalchemy" not in deps


@pytest.mark.parametrize(
    ("version", "spec", "ok"),
    [
        ("3.2.2", ">=2.0, <4", True),
        ("4.0.1", ">=2.0, <4", False),
        ("2.7.18", ">=3.0", False),
        ("3.2.0.RELEASE", ">=3.2", True),
        ("6.0.0-M1", "<6", False),
        ("0.115.12", ">=0.100, <1", True),
        ("1.4", "==1.4.0", True),
    ],
)
def test_in_range(version: str, spec: str, ok: bool) -> None:
    assert in_range(version, spec) is ok


def test_parse_version() -> None:
    assert parse_version("3.2.0.RELEASE") == (3, 2, 0)
    assert parse_version("1.0.0-RC1") == (1, 0, 0)


def _pack(name: str, versions: dict[str, str]) -> Pack:
    return Pack(name, "0", "java", "core", "", {}, Path("."), [], versions=versions)


def test_coverage_reports_untested_and_unknown_versions(repo: Path) -> None:
    items: dict = {}

    class Item(list):
        def add(self, sample: dict) -> None:
            self.append(sample)

    def item(kind: str, signal: str, target: str, label: str) -> Item:
        return items.setdefault((signal, target), Item())

    packs = [
        _pack("web", {"org.springframework.boot": ">=3.0, <4"}),  # 2.7.18 untested, 3.2.2 fine
        _pack("okhttp", {"com.squareup.okhttp3:okhttp": ">=3.0, <6"}),  # fine
        # Two alternative libraries: only the one the build states is checked
        _pack(
            "httpclient",
            {
                "org.apache.httpcomponents.client5:httpclient5": ">=5.0",
                "org.apache.httpcomponents:httpclient": ">=5",
            },
        ),
        _pack("redis", {"redis.clients:jedis": ">=4"}),  # no version stated anywhere
    ]
    _versions(packs, dependencies(repo), item)
    assert set(items) == {
        ("version_untested", "web · org.springframework.boot 2.7.18"),
        ("version_unknown", "redis · version"),
    }
    assert items[("version_untested", "web · org.springframework.boot 2.7.18")][0]["file"] == (
        "app/build.gradle"
    )
