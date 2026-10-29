import re
import httpx
import pytest

from kaisen.picopaas import FakePicoPaas, PicoPaasError, RealPicoPaas, _detail, _plain

KEY = "fake-picopaas-key-1234"


def client(handler):
    return RealPicoPaas("https://pp.test/", KEY, transport=httpx.MockTransport(handler))


def test_me_apply_get_logs_and_auth_header():
    seen = []

    def h(req):
        seen.append((req.method, req.url.path, req.headers["authorization"]))
        if req.url.path == "/auth/me":
            return httpx.Response(200, json={"email": "o@example.test", "app_limit_effective": 3})
        if req.url.path == "/apps/spec":
            return httpx.Response(200, json={"id": 7, "hostname": "k.pp.test", "status": "pending"})
        if req.url.path == "/apps/7/logs":
            assert req.url.params["tail"] == "5"
            return httpx.Response(200, json={"lines": ["a", 2]})
        return httpx.Response(200, json={"id": 7, "hostname": None, "status": None})
    c = client(h)
    me = c.me()
    assert me.email == "o@example.test" and me.app_limit == 3
    assert c.apply_spec("spec", "proj").id == 7
    assert c.apply_spec("spec", None).hostname == "k.pp.test"
    got = c.get_app(7)
    assert got.hostname == "" and got.status == "unknown"
    assert c.logs(7, tail=5) == "a\n2"
    assert all(a == f"Bearer {KEY}" for _, _, a in seen)


@pytest.mark.parametrize("code,text", [
    (401, "rejected the API key"), (403, "refused the request (403): nope"), (404, "not found"),
    (422, "rejected the app spec: bad port"), (500, "error 500"),
])
def test_http_errors_are_plain_and_key_free(code, text):
    body = {"detail": {401: "x", 403: "nope", 404: "x", 422: [{"msg": "bad port"}], 500: "boom"}[code]}
    c = client(lambda req: httpx.Response(code, json=body))
    with pytest.raises(PicoPaasError, match=re.escape(text)) as e:
        c.me()
    assert KEY not in str(e.value)


def test_network_error_names_exception_type():
    def h(req):
        raise httpx.ConnectTimeout("t", request=req)
    with pytest.raises(PicoPaasError, match=r"Could not reach PicoPaaS .*ConnectTimeout"):
        client(h).me()


def test_detail_fallbacks():
    assert _detail(httpx.Response(502, text="<html>bad gateway</html>")) == "<html>bad gateway</html>"
    assert _detail(httpx.Response(422, json={"detail": ["plain", {"msg": "m"}]})) == "plain; m"
    assert "PicoPaaS error 418" in _plain(httpx.Response(418, text="teapot"))


def test_probe_status_and_no_answer():
    assert client(lambda r: httpx.Response(302)).probe("https://a.test/") == 302

    def h(req):
        raise httpx.ConnectError("x", request=req)
    assert client(h).probe("https://a.test/") is None


def test_fake_scripts_statuses_and_errors():
    f = FakePicoPaas(statuses=["pending", "running"], http_codes=[None, 200])
    assert [f.get_app(1).status for _ in range(3)] == ["pending", "running", "running"]
    assert [f.probe("u") for _ in range(3)] == [None, 200, 200]
    assert f.logs(1)
    f.apply_error = "limit reached"
    with pytest.raises(PicoPaasError, match="limit reached"):
        f.apply_spec("s", None)
    f.auth_error = "bad key"
    with pytest.raises(PicoPaasError):
        f.me()
