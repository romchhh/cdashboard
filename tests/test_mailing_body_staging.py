from web.mailing_body_staging import delete_staged_body, load_staged_body, save_staged_body


def test_staged_body_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("web.mailing_body_staging._STAGING_DIR", tmp_path)
    key = save_staged_body(b"<p>hello</p>", user="admin")
    assert load_staged_body(key, user="admin") == "<p>hello</p>"
    delete_staged_body(key, user="admin")
    assert load_staged_body(key, user="admin") is None
