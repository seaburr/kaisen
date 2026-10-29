from kaisen import db, settings_store
from kaisen.github import GitHubError
from kaisen.models import Setting
from tests.conftest import save_settings


def test_secrets_write_only_and_encrypted(admin):
    save_settings(admin, anthropic_api_key="fake-anthropic-key-abcd", github_token="fake-github-token-wxyz")
    page = admin.client.get("/settings").text
    assert "set, ends in …abcd" in page and "set, ends in …wxyz" in page
    assert "fake-anthropic-key-abcd" not in page and "fake-github-token-wxyz" not in page
    with db.session_scope() as s:
        row = s.get(Setting, "anthropic_api_key")
        assert row.secret and "fake-anthropic-key" not in row.value
        assert settings_store.load(s).anthropic_api_key == "fake-anthropic-key-abcd"
    # blank keeps, clear removes
    save_settings(admin)
    assert "ends in …abcd" in admin.client.get("/settings").text
    save_settings(admin, clear_anthropic_api_key="1")
    assert "not set" in admin.client.get("/settings").text


def test_validation(admin):
    r = admin.post("/settings", model="gpt-nope", effort="medium", cap_run="2", cap_day="8", cap_project="30")
    assert r.status_code == 400 and "Unknown model" in r.text
    r = admin.post("/settings", model="claude-opus-5-5", effort="medium", cap_run="-1", cap_day="8", cap_project="30")
    assert r.status_code == 400


def test_test_buttons(admin):
    r = admin.post("/settings/test/anthropic")
    assert "not set" in admin.client.get(r.headers["location"]).text
    save_settings(admin, anthropic_api_key="fake-anthropic-key-abcd", github_repo="me/app", github_token="fake-github-token-wxyz")
    admin.post("/settings/test/anthropic")
    assert "Anthropic OK: model claude-opus-5-5" in admin.client.get("/settings").text
    admin.model.model_ok = False
    admin.post("/settings/test/anthropic")
    assert "Test failed" in admin.client.get("/settings").text
    admin.post("/settings/test/github")
    assert "can push to me/app" in admin.client.get("/settings").text
    admin.gh.can_push = False
    admin.post("/settings/test/github")
    assert "no push permission" in admin.client.get("/settings").text


def test_pause_toggle(admin):
    admin.post("/settings/pause")
    assert "Pause-all is <strong>ON" in admin.client.get("/settings").text
    admin.post("/settings/pause")
    assert "Pause-all is <strong>off" in admin.client.get("/settings").text
