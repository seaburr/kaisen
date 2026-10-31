from kaisen import db, settings_store
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
    save_settings(admin, anthropic_api_key="fake-anthropic-key-abcd", output_mode="github", github_url="https://github.com/me/app", github_token="fake-github-token-wxyz")
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


def test_parse_repo():
    from kaisen.settings_store import parse_repo
    for ok in ("https://github.com/me/app", "https://github.com/me/app.git", "http://github.com/me/app/", "me/app"):
        assert parse_repo(ok) == "me/app", ok
    assert parse_repo("https://github.com/me/my.site.git") == "me/my.site"
    for bad in ("https://gitlab.com/me/app", "app", "https://github.com/me", "https://github.com/me/app/tree/main", ""):
        assert parse_repo(bad) == "", bad


def test_github_mode_needs_url_and_token(admin):
    base = dict(model="claude-opus-5-5", effort="medium", cap_run="2", cap_day="8", cap_project="30", output_mode="github")
    r = admin.post("/settings", **base, github_url="")
    assert r.status_code == 400 and "repository URL" in r.text
    r = admin.post("/settings", **base, github_url="https://gitlab.com/me/app", github_token="fake-github-token-wxyz")
    assert r.status_code == 400 and "github.com/owner/repo" in r.text
    r = admin.post("/settings", **base, github_url="https://github.com/me/app")
    assert r.status_code == 400 and "access token" in r.text
    save_settings(admin, output_mode="github", github_url="https://github.com/me/app", github_token="fake-github-token-wxyz")
    save_settings(admin, output_mode="github", github_url="https://github.com/me/app")  # stored token is kept
    page = admin.client.get("/settings").text
    assert 'value="https://github.com/me/app"' in page and "ends in …wxyz" in page
    assert "personal-access-tokens/new" in page and ">Test GitHub</button>" in page


def test_download_only_hides_github_test(admin):
    page = admin.client.get("/settings").text
    assert 'id="mode_download" name="output_mode" value="download" checked' in page and ">Test GitHub</button>" not in page
