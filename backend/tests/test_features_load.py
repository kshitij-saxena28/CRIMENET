def test_every_feature_module_loads_and_mounts():
    from backend.app import features
    import backend.app.main  # noqa: F401  (mounts features)
    assert features.LOAD_ERRORS == {}, features.LOAD_ERRORS


def test_supervisor_role_permissions(client, login):
    from security.rbac import allowed
    assert allowed("supervisor", "approve") and allowed("admin", "approve")
    assert not allowed("investigator", "approve") and not allowed("auditor", "approve")
    assert allowed("supervisor", "write") and not allowed("investigator", "write")  # supervisors change data, investigators only verify
    assert client.get("/me", headers=login("supervisor")).json()["role"] == "supervisor"
