"""Search top-down: where the matches are comes first, and each application's best hit
takes its turn, so one application with many matches can't crowd the others off the page."""

from steno.mcp.tools import _digest_matched, _interleave, _matched, _where_summary

CLOSE = "a close spelling or an abbreviation of the words (`clt` for client)"


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


def test_a_flow_found_by_its_digest_says_which_part_matched():
    digest = (
        "POST /jobs · backend\n"
        "TRIGGER   http POST /jobs\n"
        "WRITES    embed_task (insert), job (insert)\n"
        "ENTITIES  EmbedTask, Job\n"
        "FUNCTIONS Task.run, JobService.create (+40 more)"
    )
    hit = {"label": "POST /jobs", "digest": digest}
    assert _digest_matched(hit, ["embed", "task"]) == (
        "digest: WRITES embed_task (insert); ENTITIES EmbedTask"
    )
    # No item has every word: those with some, with "(+N more)" left off
    assert _digest_matched(hit, ["create", "run"]) == (
        "digest: FUNCTIONS Task.run, JobService.create"
    )
    # Its name already says why
    assert _digest_matched(hit, ["jobs"]) is None
    # Found, but by nothing the words spell out: a typo or an abbreviation matched
    assert _digest_matched({"label": "x"}, ["job"]) == CLOSE


def test_an_application_found_by_its_summary_shows_its_line():
    hit = {
        "label": "budget-app",
        "summary": "Links bank accounts through Plaid to track transactions.",
        "summary_line": "Backend for a personal budgeting app.",
    }
    assert _digest_matched(hit, ["plaid"]) == "summary: Backend for a personal budgeting app."
    assert _digest_matched(hit, ["kafka"]) == CLOSE
    # A stem matches too: "categorize" is in "categories"
    hit["summary"] = "Suggests categories for transactions."
    assert _digest_matched(hit, ["categorize"]).startswith("summary:")


def test_an_item_with_operations_stays_whole():
    hit = {
        "label": "POST /x",
        "digest": "POST /x · a\nWRITES    orders (delete, upsert), job (insert)",
    }
    assert _digest_matched(hit, ["orders"]) == "digest: WRITES orders (delete, upsert)"
