

# ==================== Dashboard 视窗自注册 ====================

class FakeDashboard:
    def __init__(self):
        self.views = {}
        self.verify_ok = False
        self.verified_tokens = []

    def register_view(self, **kw):
        self.views[kw["id"]] = kw
        return True

    def unregister_view(self, view_id):
        return self.views.pop(view_id, None) is not None

    def verify_token(self, token):
        self.verified_tokens.append(token)
        return self.verify_ok


class FakeRouter:
    def __init__(self):
        self.routes = {}

    def register_http_route(self, module, path, handler, methods=None):
        for m in (methods or ["GET"]):
            self.routes[(module, path, m)] = handler

    def unregister_http_route(self, module, path):
        removed = [k for k in self.routes if k[0] == module and k[1] == path]
        for k in removed:
            self.routes.pop(k, None)
        return bool(removed)


class _Req:
    def __init__(self, token="", raw_token=None, body=None):
        self.headers = {"authorization": "Bearer " + token} if token else {}
        self.query_params = {"token": raw_token} if raw_token else {}
        self._body = body or {}

    async def json(self):
        return self._body


def make_dash():
    import QvQMemes.dashboard as dash_mod
    from fakes import FakeSDK, make_config
    from QvQMemes.library import StickerLibrary
    from QvQMemes.collect import Collector

    sdk = FakeSDK()
    sdk.Dashboard = FakeDashboard()
    sdk.router = FakeRouter()
    main = type("M", (), {})()
    main.sdk = sdk
    main.logger = sdk.logger
    main.cfg = make_config()
    main.library = StickerLibrary(sdk, main.cfg)
    main.collector = Collector(sdk, main.cfg, main.library)
    integration = dash_mod.DashboardIntegration(main)
    return integration, main, sdk


def test_setup_registers_view_and_routes():
    integration, main, sdk = make_dash()
    assert integration.setup() is True
    view = sdk.Dashboard.views.get("QvQMemes")
    assert view and view["group"] == "qvq" and view["group_title"]
    assert view["iframe_url"] == "/QvQMemes/view"
    assert ("QvQMemes", "/view", "GET") in sdk.router.routes
    assert ("QvQMemes", "/api/data", "GET") in sdk.router.routes
    assert ("QvQMemes", "/api/img", "GET") in sdk.router.routes
    integration.teardown()
    assert not sdk.Dashboard.views
    assert not [k for k in sdk.router.routes if k[0] == "QvQMemes"]


def test_auth_host_token_and_own_token():
    integration, main, sdk = make_dash()
    integration.setup()
    # 宿主令牌分支
    sdk.Dashboard.verify_ok = True
    req = _Req(token="host-token")
    assert integration._authorized(req) is True
    # 宿主拒绝 → 自管 token 兜底（首次失败校验时自动生成落 storage）
    sdk.Dashboard.verify_ok = False
    assert integration._authorized(_Req(token="wrong")) is False
    own = main.sdk.storage.get("QvQMemes:auth_token")
    assert own and integration._authorized(_Req(token=own)) is True
    assert integration._authorized(_Req(token="wrong")) is False
    assert integration._authorized(_Req()) is False


def test_data_and_save_and_delete_flow():
    import asyncio

    integration, main, sdk = make_dash()
    integration.setup()
    lib = main.library
    lib.add(path="/tmp/x.png", desc="猫猫", tags="猫")
    sdk.Dashboard.verify_ok = True

    data = asyncio.run(integration._api_data(_Req(token="t")))
    assert data["available"] is True and data["count"] >= 1
    assert data["items"][0]["desc"] == "猫猫"

    req = _Req(token="t")
    req._body = {"id": data["items"][0]["id"], "desc": "狗狗", "tags": "狗"}
    saved = asyncio.run(integration._api_save(req))
    assert saved["ok"] is True
    assert lib.get(data["items"][0]["id"])["desc"] == "狗狗"

    req._body = {"id": data["items"][0]["id"]}
    deleted = asyncio.run(integration._api_delete(req))
    assert deleted["ok"] is True


def test_view_page_returns_html():
    import asyncio

    integration, main, sdk = make_dash()
    from starlette.responses import HTMLResponse

    resp = asyncio.run(integration._view_page(_Req()))
    assert isinstance(resp, HTMLResponse) and "QvQMemes/api/data" in resp.body.decode()
