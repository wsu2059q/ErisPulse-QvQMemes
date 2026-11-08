"""Dashboard 视窗自注册（生态标准模式，ConnHub/Cron 的 setup/teardown 范式）。

模块自己向宿主 Dashboard 注册「表情包管理」视窗（iframe 模式）+ 自己的
HTTP 路由（页面/数据 API/图片代理），不依赖也不侵入 QvQChat：

- ``sdk.Dashboard.register_view(group="qvq")``：挂进 QvQ 生态分组
  （宿主对重复注册是覆盖语义，热重载安全；宿主持久化注册表——
  **on_unload 必须调用 teardown() 反注册**，否则留幽灵视窗）；
- ``self._router().register_http_route("QvQMemes", ...)``：命名空间路由，
  模块卸载时框架按 owner 兜底回收，teardown 里仍显式反注册更稳；
- 鉴权自管（与 QvQChat 同款双令牌模式）：宿主 Dashboard 令牌
  （verify_request/verify_token）优先，自管 token 兜底（storage 自动
  生成 + 首访控制台告警）；token 可走 ``Authorization: Bearer`` 头或
  ``?token=`` 查询参数（图片 ``<img src>`` 用 query 形式）；
- 页面是同源 iframe：宿主渲染时会自动在 src 上追加 ``?token=``，
  页面 JS 复刻 QvQChat 的令牌链（宿主 localStorage → URL → 自管缓存）
  并在探测成功后剥掉 URL 上的令牌。
"""

from __future__ import annotations

import base64
import html as _html
import secrets

try:
    from ErisPulse import sdk
except ImportError:  # 独立运行/测试环境兜底
    sdk = None

TOKEN_STORAGE_KEY = "QvQMemes:auth_token"
VIEW_ID = "QvQMemes"

ICON_SVG = (
    "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' "
    "stroke-linecap='round' stroke-linejoin='round'>"
    "<path d='M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z'/>"
    "<path d='M14 3v6h6'/><circle cx='9.5' cy='12.5' r='1'/><circle cx='14.5' cy='12.5' r='1'/>"
    "<path d='M9 16.5a3.4 3.4 0 0 0 6 0'/></svg>"
)


def _esc(text) -> str:
    return _html.escape(str(text or ""), quote=True)


# 单页（服务端内联 CSS/JS；文案 zh/en 按 navigator.language 切换；
# 令牌链复刻 QvQChat view 页：宿主 localStorage → URL ?token= → 自管缓存）
VIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QvQMemes</title>
<style>
:root {
  --bg: #f6f7f9; --surface: #ffffff; --tx: #26313d; --tx-s: #5b6b7c; --tx-t: #96a1ad;
  --bd: #e5e8ec; --bd-h: #d0d6dd; --accent: #3aa5dc; --accent-fill: #2f93cd;
  --accent-soft: rgba(58,165,220,.14); --danger: #e04f7c;
}
[data-theme="dark"] { --bg:#0f1115; --surface:#1c2027; --tx:#e6e9f0; --tx-s:#98a2b0; --tx-t:#5f6875;
  --bd:#272c35; --bd-h:#343b47; --accent:#74d0f5; --accent-fill:#2f93cd; --accent-soft:rgba(116,208,245,.16);
  --danger:#ff93ac; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1115; --surface:#1c2027;
  --tx:#e6e9f0; --tx-s:#98a2b0; --tx-t:#5f6875; --bd:#272c35; --bd-h:#343b47;
  --accent:#74d0f5; --accent-fill:#2f93cd; --accent-soft:rgba(116,208,245,.16); --danger:#ff93ac; } }
* { box-sizing: border-box; }
body { margin:0; padding:18px; background:var(--bg); color:var(--tx);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
.stats { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
.stat { background:var(--surface); border:1px solid var(--bd); border-radius:12px; padding:10px 18px; min-width:96px; }
.stat b { display:block; font-size:20px; }
.stat span { font-size:12px; color:var(--tx-s); }
.toolbar { display:flex; gap:10px; margin-bottom:14px; }
input { font:inherit; color:var(--tx); background:var(--surface); border:1px solid var(--bd);
  border-radius:9px; padding:8px 12px; outline:none; }
input:focus-visible, button:focus-visible, select:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
#search { flex:1; max-width:340px; }
button { font:inherit; font-weight:600; color:#fff; background:var(--accent-fill); border:none;
  border-radius:9px; padding:8px 14px; cursor:pointer; }
button.ghost { background:var(--surface); color:var(--tx-s); border:1px solid var(--bd); }
button.ghost:hover { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); }
button.danger { background:transparent; color:var(--danger); border:1px solid var(--bd); }
button:hover { filter:brightness(1.06); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
.card { display:flex; flex-direction:column; gap:6px; padding:10px; background:var(--surface);
  border:1px solid var(--bd); border-radius:12px; min-width:0; }
.card img { width:100%; height:110px; object-fit:contain; background:var(--bg); border-radius:8px; }
.desc { font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tags { display:flex; flex-wrap:wrap; gap:4px; min-height:18px; }
.tag { font-size:10px; padding:1px 7px; border-radius:999px; background:var(--accent-soft); color:var(--accent); }
.acts { display:flex; gap:6px; }
.acts button { flex:1; padding:4px 6px; font-size:11px; }
.empty { padding:40px; text-align:center; color:var(--tx-s); border:1px dashed var(--bd); border-radius:12px; }
#login { position:fixed; inset:0; background:var(--bg); display:none; align-items:center; justify-content:center; }
#login .box { background:var(--surface); border:1px solid var(--bd); border-radius:14px; padding:26px; max-width:340px; }
#login input { width:100%; margin:8px 0 12px; }
.hint { font-size:12px; color:var(--tx-t); margin-top:10px; }
</style>
</head>
<body data-theme="dark">
<div id="login"><div class="box">
  <b data-t="login_title">需要访问令牌</b>
  <input type="password" id="login-token" data-ph="login_ph" placeholder="粘贴 Dashboard 令牌" />
  <button id="login-btn" data-t="login_btn">验证并进入</button>
  <div class="hint" data-t="login_hint">令牌可从 Dashboard 设置页复制；验证后仅保存在本浏览器。</div>
</div></div>
<div class="stats" id="stats"></div>
<div class="toolbar">
  <input id="search" data-ph="search_ph" placeholder="搜索描述/标签…" />
  <button id="add" data-t="add">收录</button>
</div>
<div class="grid" id="grid"><div class="empty" data-t="loading">加载中…</div></div>
<script>
(function () {
  "use strict";
  var zh = (navigator.language || "zh-CN").indexOf("zh") === 0;
  var T = {
    login_title: zh ? "需要访问令牌" : "Access token required",
    login_ph: zh ? "粘贴 Dashboard 令牌" : "Paste the Dashboard token",
    login_btn: zh ? "验证并进入" : "Verify & enter",
    login_hint: zh ? "令牌可从 Dashboard 设置页复制；验证后仅保存在本浏览器。" :
      "Copy the token from the Dashboard settings page; it is stored in this browser only.",
    search_ph: zh ? "搜索描述/标签…" : "Search description/tags…",
    add: zh ? "收录" : "Add",
    loading: zh ? "加载中…" : "Loading…",
    unavailable: zh ? "库不可用（视觉模型或存储异常）" : "Library unavailable",
    empty: zh ? "表情包库是空的：群里发图会自动收录，或点「收录」手动添加。" :
      "The library is empty: images posted in chats are collected automatically, or click Add.",
    edit: zh ? "编辑" : "Edit", del: zh ? "删除" : "Delete", save: zh ? "保存" : "Save",
    cancel: zh ? "取消" : "Cancel", del_confirm: zh ? "删除这条表情包？本地文件一并删除。" :
      "Delete this sticker? The local file is removed too.",
    desc_label: zh ? "描述" : "Description", tags_label: zh ? "标签（空格分隔）" : "Tags (space separated)",
    untagged: zh ? "未分类" : "untagged", saved: zh ? "已保存" : "Saved",
    deleted: zh ? "已删除" : "Deleted", tagged: zh ? "已打标" : "Tagged",
    autofill: zh ? "AI 打标" : "AI tag", add_url: zh ? "图片 URL" : "Image URL",
    add_desc: zh ? "描述（留空则 AI 打标）" : "Description (empty = AI tagging)",
    add_hint: zh ? "收录后 desc/tags 留空将走视觉模型自动打标签。" :
      "Empty desc/tags triggers automatic vision tagging.",
    stat_total: zh ? "总量" : "Total", stat_cap: zh ? "上限" : "Capacity",
    stat_untagged: zh ? "未分类" : "Untagged", stat_today: zh ? "今日" : "Today",
    err: zh ? "出错" : "Error"
  };
  document.querySelectorAll("[data-t]").forEach(function (n) { n.textContent = T[n.getAttribute("data-t")] || n.textContent; });
  document.querySelectorAll("[data-ph]").forEach(function (n) { n.placeholder = T[n.getAttribute("data-ph")] || n.placeholder; });

  var TOKEN = "";
  function stripUrlToken() {
    try {
      if (!window.location.search || window.location.search.indexOf("token=") < 0) return;
      var clean = window.location.search.replace(/([?&])token=[^&]*&?/i, "$1").replace(/[?&]$/, "");
      window.history.replaceState(null, "", window.location.pathname + clean + window.location.hash);
    } catch (e) {}
  }
  function candidates() {
    var list = [];
    try { var t = localStorage.getItem("__ep_tk__"); if (t) list.push(t); } catch (e) {}
    try {
      var m = window.location.search.match(/[?&]token=([^&]+)/);
      if (m) list.push(decodeURIComponent(m[1]));
    } catch (e) {}
    try { var own = localStorage.getItem("qvq_memes_token"); if (own) list.push(own); } catch (e) {}
    return list;
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "Authorization": "Bearer " + TOKEN }, opts.headers || {});
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
      return r.json();
    });
  }
  function showLogin() { document.getElementById("login").style.display = "flex"; }
  function resolveToken() {
    var list = candidates();
    if (!list.length) { showLogin(); return Promise.resolve(false); }
    var idx = 0;
    function tryNext() {
      if (idx >= list.length) { showLogin(); return Promise.resolve(false); }
      var t = list[idx++];
      TOKEN = t;
      return fetch("/QvQMemes/api/auth", { headers: { "Authorization": "Bearer " + t } })
        .then(function (r) {
          if (r.ok) {
            try { localStorage.setItem("qvq_memes_token", t); } catch (e) {}
            stripUrlToken();
            return true;
          }
          return tryNext();
        })
        .catch(function () { return tryNext(); });
    }
    return tryNext();
  }
  document.getElementById("login-btn").onclick = function () {
    var v = document.getElementById("login-token").value.trim();
    if (!v) return;
    TOKEN = v;
    fetch("/QvQMemes/api/auth", { headers: { "Authorization": "Bearer " + v } }).then(function (r) {
      if (r.ok) {
        try { localStorage.setItem("qvq_memes_token", v); } catch (e) {}
        document.getElementById("login").style.display = "none";
        render(document.getElementById("search").value.trim());
      } else { alert(T.err); }
    });
  };

  function imgSrc(id) { return "/QvQMemes/api/img?id=" + encodeURIComponent(id) + "&token=" + encodeURIComponent(TOKEN); }
  function esc(s) { var d = document.createElement("div"); d.textContent = String(s || ""); return d.innerHTML; }

  function render(query) {
    var grid = document.getElementById("grid");
    grid.innerHTML = '<div class="empty">' + esc(T.loading) + "</div>";
    api("/QvQMemes/api/data?query=" + encodeURIComponent(query || "")).then(function (data) {
      if (data.available === false) { grid.innerHTML = '<div class="empty">' + esc(T.unavailable) + "</div>"; return; }
      var st = data.stats || {};
      var statsHtml = [["stat_total", st.total], ["stat_cap", st.capacity], ["stat_untagged", st.untagged], ["stat_today", st.today]]
        .filter(function (p) { return p[1] != null; })
        .map(function (p) { return '<div class="stat"><b>' + esc(p[1]) + '</b><span>' + esc(T[p[0]]) + "</span></div>"; }).join("");
      document.getElementById("stats").innerHTML = statsHtml;
      var items = data.items || [];
      if (!items.length) { grid.innerHTML = '<div class="empty">' + esc(T.empty) + "</div>"; return; }
      grid.innerHTML = items.map(function (it) {
        var tags = (it.tags || []).map(function (tg) { return '<span class="tag">' + esc(tg) + "</span>"; }).join("");
        var desc = it.desc || T.untagged;
        return '<div class="card" data-id="' + esc(it.id) + '">' +
          '<img loading="lazy" src="' + imgSrc(it.id) + '" alt=""/>' +
          '<div class="desc" title="' + esc(desc) + '">' + esc(desc) + "</div>" +
          '<div class="tags">' + tags + "</div>" +
          '<div class="acts"><button data-act="edit">' + esc(T.edit) + '</button>' +
          '<button data-act="auto">' + esc(T.autofill) + '</button>' +
          '<button data-act="del" class="danger">' + esc(T.del) + "</button></div></div>";
      }).join("");
      grid.querySelectorAll(".card").forEach(function (card) {
        var id = card.getAttribute("data-id");
        var item = items.filter(function (x) { return String(x.id) === String(id); })[0] || {};
        card.querySelector('[data-act="edit"]').onclick = function () { openEditor(item); };
        card.querySelector('[data-act="del"]').onclick = function () {
          if (!confirm(T.del_confirm)) return;
          api("/QvQMemes/api/delete", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: id }) }).then(function (r) {
            if (r && r.ok) { render(document.getElementById("search").value.trim()); } else { alert((r && r.error) || T.err); }
          });
        };
        card.querySelector('[data-act="auto"]').onclick = function () {
          var btn = card.querySelector('[data-act="auto"]');
          btn.disabled = true;
          api("/QvQMemes/api/autofill", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: id }) }).then(function (r) {
            btn.disabled = false;
            if (r && r.ok) { render(document.getElementById("search").value.trim()); }
            else { alert((r && r.error) || T.err); }
          }, function () { btn.disabled = false; });
        };
      });
    }, function () { grid.innerHTML = '<div class="empty">' + esc(T.err) + "</div>"; });
  }

  function modal(title, bodyHtml, onSave) {
    var mask = document.createElement("div");
    mask.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:99";
    mask.innerHTML = '<div style="background:var(--surface);border:1px solid var(--bd);border-radius:14px;padding:22px;width:min(420px,92vw)">' +
      '<b>' + esc(title) + '</b><div style="margin-top:12px">' + bodyHtml + '</div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">' +
      '<button class="ghost" data-c>' + esc(T.cancel) + '</button><button data-s>' + esc(T.save) + "</button></div></div>";
    document.body.appendChild(mask);
    mask.querySelector("[data-c]").onclick = function () { mask.remove(); };
    mask.querySelector("[data-s]").onclick = function () { onSave(mask, function () { mask.remove(); }); };
    return mask;
  }

  function openEditor(item) {
    var mask = modal(T.edit,
      '<label class="hint">' + esc(T.desc_label) + '</label><input id="m-desc" style="width:100%" value="' + esc(item.desc || "") + '"/>' +
      '<label class="hint">' + esc(T.tags_label) + '</label><input id="m-tags" style="width:100%" value="' + esc((item.tags || []).join(" ")) + '"/>',
      function (mask, close) {
        api("/QvQMemes/api/save", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: item.id, desc: mask.querySelector("#m-desc").value, tags: mask.querySelector("#m-tags").value }) })
          .then(function (r) {
            if (r && r.ok) { close(); render(document.getElementById("search").value.trim()); }
            else { alert((r && r.error) || T.err); }
          });
      });
    var d = mask.querySelector("#m-desc");
    if (d) d.focus();
  }

  document.getElementById("add").onclick = function () {
    modal(T.add,
      '<label class="hint">' + esc(T.add_url) + '</label><input id="m-url" style="width:100%" placeholder="https://…/sticker.png"/>' +
      '<label class="hint">' + esc(T.desc_label) + '</label><input id="m-desc" style="width:100%" placeholder="' + esc(T.add_desc) + '"/>' +
      '<label class="hint">' + esc(T.tags_label) + '</label><input id="m-tags" style="width:100%" placeholder="tag1 tag2"/>' +
      '<div class="hint">' + esc(T.add_hint) + "</div>",
      function (mask, close) {
        var url = mask.querySelector("#m-url").value.trim();
        if (!url) return;
        api("/QvQMemes/api/save", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url, desc: mask.querySelector("#m-desc").value, tags: mask.querySelector("#m-tags").value }) })
          .then(function (r) {
            if (r && r.ok) { close(); render(""); }
            else { alert((r && (r.error || r.message)) || T.err); }
          });
      });
  };

  var timer = 0;
  document.getElementById("search").oninput = function () {
    clearTimeout(timer);
    timer = setTimeout(function () { render(document.getElementById("search").value.trim()); }, 300);
  };

  // 主题跟随宿主（同源 iframe 读父页 data-theme）
  try {
    var parentDoc = window.parent && window.parent.document;
    if (parentDoc) {
      var apply = function () {
        var dt = parentDoc.documentElement.getAttribute("data-theme") || "";
        if (dt) document.documentElement.setAttribute("data-theme", dt);
      };
      apply();
      new MutationObserver(apply).observe(parentDoc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }
  } catch (e) {}

  resolveToken().then(function (ok) { if (ok) render(""); });
})();
</script>
</body>
</html>
"""

# 视窗标题五语言（宿主 register_view 的 titles 形态）
VIEW_TITLES = {"zh": "表情包管理", "zh-TW": "表情包管理", "en": "Sticker Manager",
               "ja": "スタンプ管理", "ru": "Менеджер стикеров"}


class DashboardIntegration:
    """表情包管理视窗：宿主 Dashboard 注册 + 模块命名空间 HTTP 路由。

    setup/teardown 必须成对（宿主把注册持久化进自己的 storage，
    模块卸载不自清会留幽灵视窗）。
    """

    def __init__(self, main):
        self.main = main
        self._registered = False
        self._routes: list[tuple[str, str]] = []

    # ---------- 生命周期 ----------

    def setup(self) -> bool:
        if self._registered:
            return True
# sdk.Dashboard 属性由框架在模块加载完成后注入（hasattr 即宿主在位）
        host = self._host()
        if host is None:
            return False
        try:
            host.register_view(
                id=VIEW_ID,
                title=VIEW_TITLES["zh"],
                titles=dict(VIEW_TITLES),
                icon_svg=ICON_SVG,
                iframe_url="/QvQMemes/view",
                group="qvq",
                group_title="QvQ 生态",
                group_title_en="QvQ Ecosystem",
            )
            self._router().register_http_route("QvQMemes", "/view", self._view_page, methods=["GET"])
            self._router().register_http_route("QvQMemes", "/api/auth", self._guard(self._api_auth), methods=["GET"])
            self._router().register_http_route("QvQMemes", "/api/data", self._guard(self._api_data), methods=["GET"])
            self._router().register_http_route("QvQMemes", "/api/save", self._guard(self._api_save), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/delete", self._guard(self._api_delete), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/autofill", self._guard(self._api_autofill), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/img", self._guard(self._api_img), methods=["GET"])
            self._registered = True
            self._routes = [
                ("/view", "GET"), ("/api/auth", "GET"), ("/api/data", "GET"),
                ("/api/save", "POST"), ("/api/delete", "POST"),
                ("/api/autofill", "POST"), ("/api/img", "GET"),
            ]
            return True
        except Exception as e:
            logger = getattr(self.main, "logger", None)
            if logger is not None:
                logger.warning(f"dashboard setup failed: {e}")
            return False

    def teardown(self) -> None:
        host = self._host()
        if self._registered and host is not None:
            try:
                host.unregister_view(VIEW_ID)
            except Exception:
                pass
            self._registered = False
        router = self._router()
        for path, method in self._routes:
            if router is None:
                break
            try:
                router.unregister_http_route("QvQMemes", path)
            except Exception:
                pass
        self._routes = []

    # ---------- 鉴权（与 QvQChat 同款双令牌模式） ----------

    def _router(self):
        return getattr(getattr(self.main, "sdk", None), "router", None)

    def _host(self):
        """宿主 Dashboard 实例（从 main.sdk 取——模块级 import ErisPulse.sdk 在
        测试/独立环境可能拿到真单例而非注入的 fake）。"""
        host = getattr(getattr(self.main, "sdk", None), "Dashboard", None)
        return host

    def _own_token(self) -> str:
        storage = getattr(self.main, "sdk", None) and self.main.sdk.storage
        stored = ""
        try:
            stored = str(storage.get(TOKEN_STORAGE_KEY, "") or "")
        except Exception:
            stored = ""
        if stored:
            return stored
        token = secrets.token_urlsafe(24)
        try:
            storage.set(TOKEN_STORAGE_KEY, token)
        except Exception:
            pass
        logger = getattr(self.main, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"[QvQMemes] Dashboard 令牌已生成（首次访问请配置）：{token}")
            except Exception:
                pass
        return token

    def _extract_token(self, request) -> str:
        raw = getattr(request, "raw", request)
        try:
            auth = raw.headers.get("authorization", "")
        except Exception:
            auth = ""
        if isinstance(auth, str) and auth.startswith("Bearer "):
            return auth[7:]
        try:
            return raw.query_params.get("token")
        except Exception:
            return None

    def _authorized(self, request) -> bool:
        provided = self._extract_token(request)
        if not provided:
            return False
        provided = str(provided)
        dashboard = self._host()
        if dashboard is not None:
            try:
                raw = getattr(request, "raw", request)
                if hasattr(dashboard, "verify_request") and dashboard.verify_request(raw):
                    return True
                if hasattr(dashboard, "verify_token") and dashboard.verify_token(provided):
                    return True
            except Exception:
                pass
        return secrets.compare_digest(provided, self._own_token())

    def _guard(self, handler):
        async def wrapped(request):
            if not self._authorized(request):
                return {"error": "Unauthorized"}, 401
            return await handler(request)

        return wrapped

    # ---------- Handlers ----------

    async def _view_page(self, request):
        from starlette.responses import HTMLResponse

        return HTMLResponse(VIEW_HTML, headers={"Cache-Control": "no-store"})

    async def _api_auth(self, request):
        return {"ok": True}

    def _library(self):
        return self.main.library

    async def _api_data(self, request):
        query = ""
        limit = 400
        try:
            qp = getattr(request, "raw", request).query_params
            query = str(qp.get("query") or "").strip()
            try:
                limit = max(1, min(int(qp.get("limit") or 400), 1000))
            except (TypeError, ValueError):
                limit = 400
        except Exception:
            pass
        lib = self._library()
        try:
            items = lib.search(query, top_n=limit) if query else lib.list(limit=limit)
        except Exception as e:
            return {"available": False, "error": str(e)}
        try:
            stats = lib.stats()
        except Exception:
            stats = {}
        return {"available": True, "stats": stats, "count": len(items), "items": items}

    async def _api_save(self, request):
        body = await request.json() or {}
        entry_id = str(body.get("id") or "").strip()
        desc = str(body.get("desc") or "")
        tags = str(body.get("tags") or "")
        if entry_id:
            updated = self._library().update(entry_id, desc=desc, tags=tags)
            if updated is None:
                return {"ok": False, "error": "not found"}
            return {"ok": True, "entry": updated}
        url = str(body.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "id or url required"}
        result = await self.main.collector.collect_url(url, source="manual")
        entry = result.get("entry")
        if entry is None:
            return {"ok": False, "error": str(result.get("error") or "collect failed")}
        if desc or tags:
            self._library().update(str(entry.get("id")), desc=desc, tags=tags)
        else:
            self.main.collector._after_added(entry)
        return {"ok": True, "id": entry.get("id")}

    async def _api_delete(self, request):
        body = await request.json() or {}
        removed = self._library().remove(str(body.get("id") or "").strip())
        if removed is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True}

    async def _api_autofill(self, request):
        body = await request.json() or {}
        entry_id = str(body.get("id") or "").strip()
        entry = self._library().get(entry_id)
        if entry is None:
            return {"ok": False, "error": "not found"}
        collector = self.main.collector
        await collector._tag_entry(entry_id)
        updated = self._library().get(entry_id)
        return {"ok": True, "desc": str((updated or {}).get("desc") or "")}

    async def _api_img(self, request):
        from pathlib import Path

        from starlette.responses import RedirectResponse, Response

        request = getattr(request, "raw", request)
        entry_id = ""
        try:
            entry_id = str(request.query_params.get("id") or "")
        except Exception:
            pass
        entry = self._library().get(entry_id)
        if entry is None:
            return Response("not found", status_code=404, media_type="text/plain")
        path = str(entry.get("path") or "").strip()
        url = str(entry.get("url") or "").strip()
        if path and not path.startswith(("http://", "https://")):
            file = Path(path).expanduser()
            if file.is_file():
                from .service import _image_mime

                return Response(file.read_bytes(),
                                media_type=_image_mime(file.suffix),
                                headers={"Cache-Control": "max-age=300"})
        if url and url.startswith(("http://", "https://")):
            return RedirectResponse(url, status_code=302)
        return Response("no image source", status_code=404, media_type="text/plain")
