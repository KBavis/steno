from steno.graph import ids


def test_step_id_is_keyed_by_function_not_position():
    flow = "flow:svc:POST /x"
    assert ids.step_id(flow, "a.B.run") == "step:flow:svc:POST /x:a.B.run"
    assert (
        ids.step_id(flow, "a.Base.run", "a.Impl", 2) == "step:flow:svc:POST /x:a.Base.run@a.Impl#2"
    )


def test_channels_are_not_scoped_to_an_application():
    assert ids.channel_id("PxChannel", "orders") == "pxchannel:orders"


def test_endpoint_id_normalizes_method():
    assert (
        ids.http_endpoint_id("users-svc", "post", "/v1/users")
        == "endpoint:users-svc:POST:/v1/users"
    )


def test_space_id_survives_renames():
    assert ids.space_id(7) == "space:7"
