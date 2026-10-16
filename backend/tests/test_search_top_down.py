"""Search top-down (D2): where the matches are comes first, and each application's best hit
takes its turn, so one application with many matches can't crowd the others off the page."""

from steno.mcp.tools import _interleave, _matched, _where_summary


def _flow(app: str, n: int) -> dict:
    return {
        "id": f"flow:{app}:{n}",
        "kind": "flow",
        "name": f"f{n}",
        "application": app,
        "space": "S1",
    }


def test_one_application_cant_crowd_out_the_others():
    hits = [_flow("steno", i) for i in range(10)] + [_flow("contextualized", i) for i in range(5)]
    hits.append(_flow("budget-app", 0))
    shown = _interleave(hits, 6)
    assert [h["application"] for h in shown[:3]] == ["steno", "contextualized", "budget-app"]
    assert {h["application"] for h in shown} == {"steno", "contextualized", "budget-app"}


def test_where_counts_every_match_by_space_and_application():
    hits = [_flow("steno", i) for i in range(3)] + [_flow("contextualized", 0)]
    hits.append({"id": "table:x", "kind": "table", "name": "ingestion_job", "space": "S1"})
    hits.append({"id": "app:y", "kind": "application", "name": "other", "space": "S2"})
    assert _where_summary(hits) == [
        {
            "space": "S1",
            "matches": 5,
            "applications": {"steno": 3, "contextualized": 1},
            "other": 1,
        },
        {"space": "S2", "matches": 1},
    ]


def test_a_match_in_the_module_path_shows_the_full_name():
    f = {"label": "_write_file", "symbol": "app.ingestible.Base._write_file", "flow": "POST /x"}
    assert _matched(f, ["ingest"]) == "function app.ingestible.Base._write_file in POST /x"
    g = {"label": "ingest_data", "symbol": "app.providers.ingest_data", "flow": "POST /y"}
    assert _matched(g, ["ingest"]) == "function ingest_data in POST /y"
