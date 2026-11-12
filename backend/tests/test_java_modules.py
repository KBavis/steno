"""A repository the Java resolver can't read whole is read module by module, and a module that
still fails is a gap while the rest are resolved."""

import contextvars
import json

from steno.resolvers import java
from steno.resolvers.java import JVM_BY_MODULE, JavaResolver, build_modules


def _write(root, rel, text="class A {}\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_sources_group_under_the_nearest_build_file(tmp_path):
    _write(tmp_path, "pom.xml", "<project/>")
    _write(tmp_path, "core/pom.xml", "<project/>")
    _write(tmp_path, "web/build.gradle.kts", "")
    files = [
        _write(tmp_path, "core/src/main/java/a/A.java"),
        _write(tmp_path, "core/src/main/java/a/b/B.java"),
        _write(tmp_path, "web/src/main/java/w/W.java"),
        _write(tmp_path, "tools/T.java"),  # no build file of its own: the root's
    ]
    modules = build_modules(tmp_path, files)
    assert {k: len(v) for k, v in modules.items()} == {"core": 2, "web": 1, ".": 1}


def _fake_helper(failing):
    def run(root, sources, jars, level, batch, parts=None):
        module = sources[0].relative_to(root).parts[0]
        if module == failing:
            return {}, "Java resolver failed: java.lang.OutOfMemoryError"
        symbol = f"{module}.A#f()"
        (parts / "exprs.jsonl").write_text(
            json.dumps({"file": f"{module}/A.java", "sl": 1, "sc": 1, "el": 1, "ec": 5,
                        "symbol": symbol}) + "\n"
        )  # fmt: skip
        (parts / "calls.jsonl").write_text(json.dumps([symbol, [{"line": 1, "method": "g"}]]))
        out = {
            "types": [{"name": f"{module}.A", "file": f"{module}/A.java", "kind": "class"}],
            "functions": [{"symbol": symbol, "name": "f", "container": f"{module}.A",
                           "file": f"{module}/A.java", "start": 1, "end": 2}],
            "imports": {f"{module}/A.java": []},
            "errors": [],
            "stats": {"files": len(sources), "functions": 1},
        }  # fmt: skip
        return out, None

    return run


def _resolve(tmp_path):
    def run():
        JVM_BY_MODULE.set(True)
        return JavaResolver(tmp_path)

    return contextvars.copy_context().run(run)


def test_a_module_that_fails_is_a_gap_and_the_others_are_resolved(tmp_path, monkeypatch):
    for module in ("core", "web", "broken"):
        _write(tmp_path, f"{module}/pom.xml", "<project/>")
        _write(tmp_path, f"{module}/A.java")
    monkeypatch.setattr(java, "run_helper", _fake_helper("broken"))
    r = _resolve(tmp_path)

    assert r.failed is None
    assert set(r.types) == {"core.A", "web.A"}
    assert r.function("web.A#f()") is not None
    assert r._calls.get("core.A#f()") == [{"line": 1, "method": "g"}]
    assert r._index.expr("web/A.java", 1, 1, 1, 5)["symbol"] == "web.A#f()"
    stats = r.stats()
    assert stats["by_module"] and stats["modules"] == 3 and stats["files"] == 2
    assert [m["module"] for m in stats["modules_failed"]] == ["broken"]
    assert [(p.name, msg.split(":")[0]) for p, msg in r.errors] == [
        ("broken", "Java resolver failed")
    ]


def test_every_module_failing_fails_the_resolver(tmp_path, monkeypatch):
    _write(tmp_path, "broken/pom.xml", "<project/>")
    _write(tmp_path, "broken/A.java")
    monkeypatch.setattr(java, "run_helper", _fake_helper("broken"))
    r = _resolve(tmp_path)
    assert r.failed and "OutOfMemoryError" in r.failed
