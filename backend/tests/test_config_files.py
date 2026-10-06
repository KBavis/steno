from steno.source.config_files import key_regex, read


def test_key_pattern_captures_named_segments():
    m = key_regex("app.kafka.topics.{NAME}").match("app.kafka.topics.orders")
    assert m and m["NAME"] == "orders"
    assert not key_regex("app.kafka.topics.{NAME}").match("app.kafka.topics.orders.retry")


def test_yaml_documents_and_properties_flatten(tmp_path):
    (tmp_path / "application.yml").write_text(
        "app:\n  kafka:\n    topics:\n      orders: o.v1\n---\napp.kafka.topics.audit: a.v1\n"
    )
    (tmp_path / "application.properties").write_text("# comment\napp.url=http://x\n")
    assert read(tmp_path / "application.yml") == {
        "app.kafka.topics.orders": "o.v1",
        "app.kafka.topics.audit": "a.v1",
    }
    assert read(tmp_path / "application.properties") == {"app.url": "http://x"}
