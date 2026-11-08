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
_HTML = """
<h1 class="page-title" data-i18n="qvqm_title">表情包管理</h1>
<p class="page-desc" data-i18n="qvqm_desc">表情包库（QvQMemes）：群内图片自动收录，AI 视觉打标并拒收截图等非表情内容</p>
<div class="stat-grid" id="qvm-stats" style="margin-bottom:14px"></div>
<div class="qvm-toolbar">
  <input id="qvm-search" class="ep-input" data-i18n-ph="qvqm_search" placeholder="搜索描述/标签…" />
  <button id="qvm-add" class="btn btn-primary" data-i18n="qvqm_add">收录</button>
</div>
<div class="card"><div class="card-body" style="padding:12px">
  <div class="qvm-grid" id="qvm-grid"><div class="qvm-empty">…</div></div>
</div></div>
"""

_CSS = """
.qvm-toolbar { display:flex; gap:8px; margin-bottom:14px; align-items:center; }
.qvm-toolbar .ep-input { max-width:320px; }
.qvm-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
.qvm-card { display:flex; flex-direction:column; gap:6px; padding:10px; background:var(--bg-t);
  border:1px solid var(--bd); border-radius:var(--radius-md); min-width:0; }
.qvm-card img { width:100%; height:110px; object-fit:contain; background:var(--bg-s); border-radius:var(--radius-xs); }
.qvm-desc { font-size:12px; color:var(--tx-p); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.qvm-tags { display:flex; flex-wrap:wrap; gap:4px; min-height:18px; }
.qvm-tag { font-size:10px; padding:2px 8px; border-radius:999px; background:var(--accent-soft); color:var(--accent); }
.qvm-acts { display:flex; gap:6px; }
.qvm-acts .btn { flex:1; padding:4px 6px; font-size:11px; }
.qvm-empty { grid-column:1/-1; padding:36px; text-align:center; color:var(--tx-s); }
"""

_JS = """
var QvqMemesView = {
  _inited: false, _timer: 0,
  init: function () {
    if (this._inited) return; // loader 每次开 tab 都执行：幂等
    this._inited = true;
    var self = this;
    var search = document.getElementById("qvm-search");
    if (search) search.addEventListener("input", function () {
      clearTimeout(self._timer);
      self._timer = setTimeout(function () { self.render(search.value.trim()); }, 300);
    });
    var add = document.getElementById("qvm-add");
    if (add) add.addEventListener("click", function () { self.openAdd(); });
    this.render("");
  },
  t: function (k) {
    var zh = (navigator.language || "zh").indexOf("zh") === 0;
    var dict = zh ? {
      search: "搜索描述/标签…", add: "收录", empty: "表情包库是空的：群里发图会自动收录，或点「收录」手动添加。",
      edit: "编辑", del: "删除", auto: "AI 打标", del_confirm: "删除这条表情包？本地文件一并删除。",
      desc: "描述", tags: "标签（空格分隔）", untagged: "未分类", saved: "已保存", deleted: "已删除",
      tagged: "已打标", add_url: "图片 URL", add_desc: "描述（留空则 AI 打标）",
      add_hint: "desc/tags 留空将走视觉模型自动打标签。",
      stat_total: "总数", stat_cap: "上限", stat_untagged: "未分类", stat_today: "今日",
      save: "保存", cancel: "取消", err: "出错", unavailable: "库不可用（视觉模型或存储异常）"
    } : {
      search: "Search description/tags…", add: "Add", empty: "Library is empty: chat images are collected automatically, or click Add.",
      edit: "Edit", del: "Delete", auto: "AI tag", del_confirm: "Delete this sticker? The local file is removed too.",
      desc: "Description", tags: "Tags (space separated)", untagged: "untagged", saved: "Saved", deleted: "Deleted",
      tagged: "Tagged", add_url: "Image URL", add_desc: "Description (empty = AI tagging)",
      add_hint: "Empty desc/tags triggers automatic vision tagging.",
      stat_total: "Total", stat_cap: "Capacity", stat_untagged: "Untagged", stat_today: "Today",
      save: "Save", cancel: "Cancel", err: "Error", unavailable: "Library unavailable"
    };
    return dict[k] || k;
  },
  headers: function () { return { "Authorization": "Bearer " + (localStorage.getItem(TK) || "") }; },
  imgSrc: function (id) { return "/QvQMemes/api/img?id=" + encodeURIComponent(id) + "&token=" + encodeURIComponent(localStorage.getItem(TK) || ""); },
  post: function (path, body) {
    return fetch(path, { method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, this.headers()),
      body: JSON.stringify(body) }).then(function (r) { return r.json(); });
  },
  render: function (query) {
    var self = this;
    var grid = document.getElementById("qvm-grid");
    if (!grid) return;
    fetch("/QvQMemes/api/data?query=" + encodeURIComponent(query || ""), { headers: this.headers() })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!grid) return;
        if (data.available === false) {
          grid.innerHTML = '<div class="qvm-empty">' + esc(data.error || self.t("unavailable")) + "</div>";
          return;
        }
        var st = data.stats || {};
        var stats = [["stat_total", st.total], ["stat_cap", st.capacity], ["stat_untagged", st.untagged], ["stat_today", st.today]]
          .filter(function (p) { return p[1] != null; })
          .map(function (p) {
            return '<div class="stat-card"><div class="stat-meta"><span class="stat-val">' + esc(p[1]) +
              '</span><span class="stat-label">' + esc(self.t(p[0])) + "</span></div></div>";
          }).join("");
        var statsBox = document.getElementById("qvm-stats");
        if (statsBox) statsBox.innerHTML = stats;
        var items = data.items || [];
        if (!items.length) { grid.innerHTML = '<div class="qvm-empty">' + esc(self.t("empty")) + "</div>"; return; }
        grid.innerHTML = items.map(function (it) {
          var tags = (it.tags || []).map(function (tg) { return '<span class="qvm-tag">' + esc(tg) + "</span>"; }).join("");
          var desc = it.desc || self.t("untagged");
          return '<div class="qvm-card" data-id="' + esc(it.id) + '">' +
            '<img loading="lazy" src="' + self.imgSrc(it.id) + '" alt=""/>' +
            '<div class="qvm-desc" title="' + esc(desc) + '">' + esc(desc) + "</div>" +
            '<div class="qvm-tags">' + tags + "</div>" +
            '<div class="qvm-acts"><button class="btn btn-secondary" data-act="edit">' + esc(self.t("edit")) + "</button>" +
            '<button class="btn btn-secondary" data-act="auto">' + esc(self.t("auto")) + "</button>" +
            '<button class="btn btn-danger" data-act="del">' + esc(self.t("del")) + "</button></div></div>";
        }).join("");
        grid.querySelectorAll(".qvm-card").forEach(function (card) {
          var id = card.getAttribute("data-id");
          var item = items.filter(function (x) { return String(x.id) === String(id); })[0] || {};
          card.querySelector('[data-act="edit"]').addEventListener("click", function () { self.openEditor(item); });
          card.querySelector('[data-act="del"]').addEventListener("click", function () {
            confirm2(self.t("del_confirm"), "").then(function (ok) {
              if (!ok) return;
              self.post("/QvQMemes/api/delete", { id: id }).then(function (r) {
                if (r && r.ok) { toast(self.t("deleted"), "ok"); self.render(document.getElementById("qvm-search").value.trim()); }
                else { toast((r && r.error) || self.t("err"), "er"); }
              });
            });
          });
          card.querySelector('[data-act="auto"]').addEventListener("click", function () {
            var btn = card.querySelector('[data-act="auto"]');
            btn.disabled = true;
            self.post("/QvQMemes/api/autofill", { id: id }).then(function (r) {
              btn.disabled = false;
              if (r && r.ok) { toast(self.t("tagged") + ": " + (r.desc || ""), "ok"); self.render(document.getElementById("qvm-search").value.trim()); }
              else { toast((r && r.error) || self.t("err"), "er"); }
            }, function () { btn.disabled = false; });
          });
        });
      });
  },
  modal: function (title, bodyHtml, onSave) {
    var mask = document.createElement("div");
    mask.className = "modal-overlay show";
    mask.innerHTML = '<div class="modal-box"><div class="modal-title">' + esc(title) + "</div>" +
      '<div class="modal-text">' + bodyHtml + "</div>" +
      '<div class="modal-actions"><button class="btn btn-secondary" data-c>' + esc(this.t("cancel")) + "</button>" +
      '<button class="btn btn-primary" data-s>' + esc(this.t("save")) + "</button></div></div>";
    document.body.appendChild(mask);
    var self = this;
    mask.querySelector("[data-c]").addEventListener("click", function () { mask.remove(); });
    mask.querySelector("[data-s]").addEventListener("click", function () { onSave(mask, function () { mask.remove(); }); });
    return mask;
  },
  openEditor: function (item) {
    var self = this;
    this.modal(this.t("edit"),
      '<div class="hint">' + esc(this.t("desc")) + '</div><input id="qvm-m-desc" class="ep-input" style="width:100%" value="' + esc(item.desc || "") + '"/>' +
      '<div class="hint">' + esc(this.t("tags")) + '</div><input id="qvm-m-tags" class="ep-input" style="width:100%;margin-bottom:6px" value="' + esc((item.tags || []).join(" ")) + '"/>',
      function (mask, close) {
        self.post("/QvQMemes/api/save", { id: item.id, desc: mask.querySelector("#qvm-m-desc").value, tags: mask.querySelector("#qvm-m-tags").value })
          .then(function (r) {
            if (r && r.ok) { toast(self.t("saved"), "ok"); close(); self.render(document.getElementById("qvm-search").value.trim()); }
            else { toast((r && r.error) || self.t("err"), "er"); }
          });
      });
  },
  openAdd: function () {
    var self = this;
    this.modal(this.t("add"),
      '<div class="hint">' + esc(this.t("add_url")) + '</div><input id="qvm-m-url" class="ep-input" style="width:100%" placeholder="https://…/sticker.png"/>' +
      '<div class="hint">' + esc(this.t("desc")) + '</div><input id="qvm-m-desc" class="ep-input" style="width:100%" placeholder="' + esc(this.t("add_desc")) + '"/>' +
      '<div class="hint">' + esc(this.t("tags")) + '</div><input id="qvm-m-tags" class="ep-input" style="width:100%;margin-bottom:6px" placeholder="tag1 tag2"/>' +
      '<div class="hint">' + esc(this.t("add_hint")) + "</div>",
      function (mask, close) {
        var url = mask.querySelector("#qvm-m-url").value.trim();
        if (!url) return;
        self.post("/QvQMemes/api/save", { url: url, desc: mask.querySelector("#qvm-m-desc").value, tags: mask.querySelector("#qvm-m-tags").value })
          .then(function (r) {
            if (r && r.ok) { close(); self.render(""); }
            else { toast((r && (r.error || r.message)) || self.t("err"), "er"); }
          });
      });
  }
};
function QvqMemesViewInit() { QvqMemesView.init(); }
"""

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
# 内联模式：页面直接活在宿主 Dashboard DOM 里，复用原生组件（btn/card/stat-grid）
# 与主题 token（--bg-t/--accent/--bd...），不再有独立 iframe 样式
            host.register_view(
                id=VIEW_ID,
                title=VIEW_TITLES["zh"],
                titles=dict(VIEW_TITLES),
                icon_svg=ICON_SVG,
                html_content=_HTML,
                js_content=_JS,
                css_content=_CSS,
                loader="QvqMemesViewInit",
                group="qvq",
                group_title="QvQ 生态",
                group_title_en="QvQ Ecosystem",
            )
            self._router().register_http_route("QvQMemes", "/api/auth", self._guard(self._api_auth), methods=["GET"])
            self._router().register_http_route("QvQMemes", "/api/data", self._guard(self._api_data), methods=["GET"])
            self._router().register_http_route("QvQMemes", "/api/save", self._guard(self._api_save), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/delete", self._guard(self._api_delete), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/autofill", self._guard(self._api_autofill), methods=["POST"])
            self._router().register_http_route("QvQMemes", "/api/img", self._guard(self._api_img), methods=["GET"])
            self._registered = True
            self._routes = [
                ("/api/auth", "GET"), ("/api/data", "GET"),
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
