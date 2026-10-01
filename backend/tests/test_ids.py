from steno.graph import ids


def test_function_id_includes_param_types_only_when_given():
    assert ids.function_id("svc", "a.B#run") == "fn:svc:a.B#run"
    assert ids.function_id("svc", "a.B#run", ["a.Req", "int"]) == "fn:svc:a.B#run(a.Req,int)"


def test_endpoint_id_normalizes_method():
    assert (
        ids.http_endpoint_id("users-svc", "post", "/v1/users")
        == "endpoint:users-svc:POST:/v1/users"
    )


def test_nested_space_id_is_its_path():
    assert ids.space_id(["Payroll", "Adjustments"]) == "space:Payroll/Adjustments"
