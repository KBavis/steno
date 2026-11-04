"""The coverage report stays quiet (D146): a guard against the noise seen on Steno's own
repository coming back. Each case is a shape that once produced many items for one thing."""

from pathlib import Path
from textwrap import dedent

from steno.config import get_settings
from steno.coverage.collect import collect
from steno.graph.build import Context, build
from steno.graph.structure import detect
from steno.ingestion.local import run_folder
from steno.rule_packs.packs import is_enabled, load_packs

FILES = {
    # A CLI: a framework calls each command through its decorator
    "app/cli.py": """
        import typer

        cli = typer.Typer()


        @cli.command()
        def ingest():
            with open("/tmp/out.txt", "w") as f:
                f.write("x")


        @cli.command()
        def reset():
            with open("/tmp/out.txt", "w") as f:
                f.write("y")


        @cli.command()
        def stats():
            with open("/tmp/out.txt") as f:
                return f.read()
    """,
    # A class whose methods nothing calls by name (an implementation behind an interface)
    "app/registry.py": """
        import requests


        class Registry:
            def get(self, key):
                return requests.get("https://registry.example.com/items")

            def save(self, key, value):
                requests.post("https://registry.example.com/items", json=value)

            def remove(self, key):
                requests.delete("https://registry.example.com/items")
    """,
    # Three methods of one I/O library no pack covers
    "app/cache.py": """
        import redis

        cache = redis.Redis()


        def warm():
            cache.get("a")
            cache.set("a", 1)
            cache.delete("a")


        warm()
    """,
    # An endpoint, so this is an application (D74 infers flows only inside one)
    "app/api.py": """
        from fastapi import APIRouter

        router = APIRouter()


        @router.get("/health")
        def health():
            return {"ok": True}
    """,
    # A settings class: its fields are code, read as settings.X
    "app/config.py": """
        from pydantic_settings import BaseSettings


        class Settings(BaseSettings):
            api_host: str = "0.0.0.0"
            worker_host: str = "0.0.0.0"
    """,
}


def _report(tmp_path: Path):
    for name, code in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(dedent(code).lstrip())
    (tmp_path / "requirements.txt").write_text(
        "fastapi\ntyper\nredis\nrequests\npydantic-settings\n"
    )
    all_packs = load_packs(get_settings().rule_packs_dir)
    packs = [p for p in all_packs if is_enabled(p, tmp_path)]
    engine = run_folder(tmp_path, packs)
    x = engine.out
    structure = detect(tmp_path, {ep.origin.file for ep in x.entry_points})
    ctx = Context("demo", "space:1", "org:1", 1, "c")
    plan = build(x, engine.resolver, structure, ctx, packs=all_packs)
    files = [p.relative_to(engine.repo).as_posix() for p in engine.files]
    used = engine.config_used | engine.resolver.properties.used
    report = collect(x, engine.resolver, plan, all_packs, files=files, read=engine.read,
                     root=engine.repo, config_used=used)  # fmt: skip
    return [i for i in report.items if not i.quiet], report


def test_one_framework_or_library_is_one_item_not_one_per_function(tmp_path):
    shown, _ = _report(tmp_path)
    by = {}
    for i in shown:
        by.setdefault(i.signal, []).append(i)
    # The CLI commands are the hook's item, not three "no known trigger" items
    assert [i.target for i in by.get("unknown_hook", [])] == ["typer.Typer.command"]
    assert not any(i.target == "app.cli" for i in by.get("inferred_entry", []))
    # The class's three methods are one item for the class
    registry = [i for i in by.get("inferred_entry", []) if i.target == "app.registry.Registry"]
    assert len(registry) == 1
    assert registry[0].label == "No known trigger · registry.Registry (3 functions)"
    # Three redis methods are one item, and the library isn't listed again beside them
    assert [i.target for i in by.get("external_call", [])] == ["redis"]
    assert "(3 methods)" in by["external_call"][0].label
    assert "library" not in by


def test_settings_fields_and_a_services_own_files_stay_out(tmp_path):
    shown, report = _report(tmp_path)
    # A settings class's fields aren't "config nothing read"
    assert not any(i.signal == "unread_config" for i in shown)
    # One application's own files are kept, quietly
    assert not any(i.signal == "file_call" for i in shown)
    assert any(i.signal == "file_call" and i.quiet for i in report.items)


def test_a_file_one_application_writes_and_another_reads_stays_visible():
    from steno.coverage.collect import Item, _quiet

    own = Item("external_call", "file_call", "joblib", "x")
    own.add({"file": "svc/a.py", "function": "a.save", "application": "svc"})
    own.add({"file": "svc/b.py", "function": "b.load", "application": "svc"})
    handoff = Item("external_call", "file_call", "joblib", "x")
    handoff.add({"file": "training/train.py", "function": "train.save", "application": ""})
    handoff.add({"file": "inference/predict.py", "function": "p.load", "application": "inference"})
    _quiet({1: own, 2: handoff})
    assert own.quiet and not handoff.quiet
