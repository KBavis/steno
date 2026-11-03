"""The write stage's record of what the graph write did (D135)."""

from types import SimpleNamespace

from steno.graph.build import GraphPlan
from steno.graph.delta import Delta
from steno.graph.writer import WriteReport
from steno.ingestion import pipeline


def test_the_stage_reports_the_graph_write_not_the_identity_report(monkeypatch):
    written = WriteReport(batches=7, nodes_written=3, edges_written=5)
    monkeypatch.setattr(pipeline, "write_plan", lambda *a, **k: (Delta(), written))
    monkeypatch.setattr(pipeline, "get_driver", lambda: None)
    monkeypatch.setattr(pipeline, "database", lambda: "neo4j")
    monkeypatch.setattr(
        pipeline, "store_claims", lambda *a, **k: SimpleNamespace(added=0, removed=0, kept=0)
    )
    monkeypatch.setattr(pipeline, "SqlRegistry", lambda session: SimpleNamespace(remove=print))
    ctx = SimpleNamespace(
        state={
            "plan": GraphPlan(),
            # the identity stage's report has its own as_dict(): it must not shadow the write's
            "identity_report": SimpleNamespace(removed_ids=[], as_dict=lambda: {"new": ["app"]}),
            "engine": SimpleNamespace(out=SimpleNamespace(claims=[])),
        },
        job=SimpleNamespace(id=1, to_sha="abc"),
        repository=SimpleNamespace(name="r", id=1, last_ingested_sha=None),
        session=SimpleNamespace(add_all=print, commit=print),
    )
    out = pipeline.write(ctx)
    assert out["write"]["batches"] == 7 and out["write"]["nodes_written"] == 3
    assert "new" not in out["write"]
    assert ctx.repository.last_ingested_sha == "abc"
