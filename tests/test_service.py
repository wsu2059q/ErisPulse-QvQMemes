"""五服务 + Main 生命周期测试：send_sticker / search / stats / add / qvq_settings、
observe 订阅、目录扫描、plugin_config 合并与卸载清理。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fakes import FakeAdapter, FakeSDK, drain_tasks, make_config

import QvQMemes
from QvQMemes.Main import OBSERVE_EVENT, SERVICES, Main


def make_main(tmp_path, handlers=None, adapters=None, **cfg_overrides):
    main = Main()
    main.sdk = FakeSDK(handlers, adapters or {"onebot11": FakeAdapter()})
    main._local_cfg = make_config(library_dir=str(tmp_path / "stickers"), **cfg_overrides)
    return main


class TestSendSticker:
    async def test_send_url_ref_and_mark_used(self, tmp_path):
        main = make_main(tmp_path)
        lib = main._library()
        entry = lib.add(url="https://x/cat.png", desc="猫大笑", tags="开心 猫")["entry"]
        result = await main.send_sticker("猫 开心", "onebot11", "12345")
        assert result.startswith("已发送")
        assert "猫大笑" in result
        adapter = main.sdk.adapter.get("onebot11")
        assert ("To", "group", "12345") in adapter.send_log
        assert ("Image", "https://x/cat.png") in adapter.send_log
        assert lib.get(entry["id"])["uses"] == 1

    async def test_send_prefers_existing_local_file(self, tmp_path):
        sticker = tmp_path / "cat.png"
        sticker.write_bytes(b"png")
        main = make_main(tmp_path)
        main._library().add(path=sticker.as_posix(), url="https://x/cat.png",
                            desc="本地猫", tags="猫")
        await main.send_sticker("猫", "onebot11", "g1")
        assert ("Image", sticker.as_posix()) in main.sdk.adapter.get("onebot11").send_log

    async def test_no_match_returns_tag_hints(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="u", desc="猫笑", tags="开心 猫")
        result = await main.send_sticker("不存在的语义", "onebot11", "g1")
        assert "没有匹配" in result
        assert "开心" in result  # 标签提示换词
        assert main.sdk.adapter.get("onebot11").send_log == []

    async def test_empty_library_hint(self, tmp_path):
        main = make_main(tmp_path)
        assert "库为空" in await main.send_sticker("猫", "onebot11", "g1")

    async def test_missing_query_and_target(self, tmp_path):
        main = make_main(tmp_path)
        assert "缺少语义描述" in await main.send_sticker("", "onebot11", "g1")
        assert "缺少发送目标" in await main.send_sticker("猫", "", "g1")
        assert "缺少发送目标" in await main.send_sticker("猫", "onebot11", "")

    async def test_adapter_missing(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="https://x/a.png", tags="猫")
        result = await main.send_sticker("猫", "nosuch", "g1")
        assert "不可用" in result
        assert "nosuch" in result

    async def test_send_method_missing_returns_error_text(self, tmp_path):
        main = make_main(tmp_path)
        main._local_cfg.send_method = "EmojiFace"  # FakeSendChain 没有该方法
        main._library().add(url="https://x/a.png", tags="猫")
        result = await main.send_sticker("猫", "onebot11", "g1")
        assert "发送失败" in result
        assert "EmojiFace" in result

    async def test_never_raises_on_garbage(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="https://x/a.png", tags="猫")
        for args in ((None,), (123,), ({"query": "猫"},), ("猫", None, None)):
            assert isinstance(await main.send_sticker(*args), str)


class TestSearchStats:
    async def test_search_dict_shape(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="u1", desc="猫笑", tags="开心 猫")
        result = await main.sticker_search("开心 猫", top_n=3)
        assert result["count"] == 1
        assert result["results"][0]["id"] == "m000001"
        assert result["results"][0]["score"] > 0
        assert result["results"][0]["tags"] == ["开心", "猫"]

    async def test_search_empty_query_gives_hints(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="u1", tags="开心")
        result = await main.sticker_search("")
        assert result["count"] == 0
        assert "开心" in result["tag_hints"]

    async def test_stats_shape(self, tmp_path):
        main = make_main(tmp_path)
        main._library().add(url="u1", desc="猫", tags="开心")
        stats = await main.sticker_stats()
        assert stats["total"] == 1
        assert stats["max_library"] == 500
        assert stats["untagged"] == 0
        assert stats["tag_distribution"]["开心"] == 1


class TestStickerAdd:
    async def test_add_local_with_desc_tags(self, tmp_path):
        f = tmp_path / "m.png"
        f.write_bytes(b"png")
        main = make_main(tmp_path)
        result = await main.sticker_add(path=str(f), desc="柴犬", tags="狗 可爱")
        assert "已手动收录" in result
        assert "柴犬" in result
        entry = main._library().list()[0]
        assert entry["path"] == f.as_posix()
        assert entry["tags"] == ["狗", "可爱"]

    async def test_add_duplicate(self, tmp_path):
        main = make_main(tmp_path)
        await main.sticker_add(url="https://x/a.png", desc="猫")
        result = await main.sticker_add(url="https://x/a.png")
        assert "已在库中" in result
        assert "m000001" in result

    async def test_add_requires_ref(self, tmp_path):
        main = make_main(tmp_path)
        assert "收录失败" in await main.sticker_add()

    async def test_add_without_desc_spawns_vision(self, tmp_path):
        f = tmp_path / "v.png"
        f.write_bytes(b"png")

        async def describe(**kw):
            return "狗子歪头\n疑惑 可爱 狗"

        main = make_main(tmp_path, handlers={("QvQChat", "describe_image"): describe})
        result = await main.sticker_add(path=str(f))
        assert "打标签进行中" in result
        await drain_tasks(main._collector())
        entry = main._library().list()[0]
        assert entry["desc"] == "狗子歪头"
        assert entry["tags"] == ["疑惑", "可爱", "狗"]

    async def test_add_url_download_failure_keeps_url(self, tmp_path):
        main = make_main(tmp_path)
        bad = (tmp_path / "nope.png").as_uri()
        result = await main.sticker_add(url=bad, desc="远端图")
        assert "已手动收录" in result
        assert main._library().list()[0]["url"] == bad
        assert main._library().list()[0]["path"] == ""


class TestQvqSettings:
    async def test_settings_is_title_marker_only(self, tmp_path):
        """配置项由 ConfigClass 声明（框架 schema 流），服务只返回归属标记。"""
        main = make_main(tmp_path)
        settings = await main.qvq_settings()
        assert settings["title"]
        assert "items" not in settings


class TestMainLifecycle:
    async def test_on_load_wires_everything(self, tmp_path):
        existing = tmp_path / "stickers"
        existing.mkdir()
        (existing / "seed.png").write_bytes(b"png")
        main = make_main(tmp_path, handlers={
            ("QvQChat", "plugin_config"): {"auto_collect": False, "max_library": "10"},
        })
        assert await main.on_load(None) is True
        # 订阅 observe
        assert main.sdk.lifecycle.registered == [(OBSERVE_EVENT, main._on_observe)]
        # 目录扫描入库
        assert len(main.library.list()) == 1
        assert main.library.list()[0]["source"] == "scan"
        # plugin_config 合并（含字符串 → int 强转）
        assert main._cfg().auto_collect is False
        assert main._cfg().max_library == 10
        assert main._cfg().collect_per_chat_daily == 20  # 未下发的键保持默认

    async def test_on_load_plugin_config_missing_is_silent(self, tmp_path):
        main = make_main(tmp_path)  # 无 plugin_config handler → 调用抛错 → 静默默认
        assert await main.on_load(None) is True
        assert main._cfg().auto_collect is True

    async def test_plugin_config_value_shapes(self, tmp_path):
        for remote in ({"values": {"recency_hours": 24}},
                       [{"key": "send_method", "value": "Image"}],
                       {"tag_via_vision": "false"}):
            main = make_main(tmp_path, handlers={("QvQChat", "plugin_config"): remote})
            assert await main.on_load(None) is True

    async def test_observe_event_flows_to_collector(self, tmp_path):
        src = tmp_path / "s.png"
        src.write_bytes(b"png")
        main = make_main(tmp_path, handlers={("QvQChat", "describe_image"): "x"})
        await main.on_load(None)
        await main.sdk.lifecycle.emit(OBSERVE_EVENT,
                                      {"chat_key": "g", "images": [src.as_uri()]})
        await drain_tasks(main.collector)
        assert len(main.library.list()) == 1

    async def test_on_unload_cleans_up(self, tmp_path):
        main = make_main(tmp_path)
        await main.on_load(None)
        pending = main.collector._spawn(_sleep_forever())
        assert await main.on_unload(None) is True
        assert main.sdk.lifecycle.unregistered
        assert pending.cancelled() or pending.done()
        await drain_tasks(main.collector)


async def _sleep_forever():
    await asyncio.sleep(3600)


class TestMeta:
    def test_services_whitelist_complete(self):
        meta = Main.get_meta()
        info = meta if isinstance(meta, dict) else vars(meta)
        names = [s["name"] for s in info["services"]]
        assert names == ["send_sticker", "sticker_search", "sticker_stats",
                         "sticker_add", "qvq_settings", "qvq_schemas", "qvq_access",
                         "qvq_extensions"]
        send = next(s for s in SERVICES if s["name"] == "send_sticker")
        assert send["description"] == ("根据语义描述从表情包库挑选并发送表情包；"
                                       "适合轻松/调侃/情绪表达场景，正式任务慎用")
        assert QvQMemes.__version__ == "0.1.0"

    def test_coerce(self):
        assert Main._coerce("10", "int") == 10
        assert Main._coerce("true", "bool") is True
        assert Main._coerce("0", "bool") is False
        assert Main._coerce("maybe", "bool") is None
        assert Main._coerce(72, "float") == 72.0
        assert Main._coerce(1, "str") == "1"


async def test_qvq_extensions_injects_sticker_usage_prompt(tmp_path):
    main = make_main(tmp_path)
    data = await main.qvq_extensions()
    assert data["prompts"] and "send_sticker" in data["prompts"][0]["text"]


async def test_send_sticker_downloads_url_only_entry(tmp_path, monkeypatch):
    """URL-only 条目发送前按需下载成本地文件（适配器不能把 http 当路径打开）。"""
    import QvQMemes.collect as collect

    calls = []

    def fake_fetch(url, timeout=10):
        calls.append(url)
        return b"\x89PNG-fake-bytes"

    monkeypatch.setattr(collect, "_fetch_bytes", fake_fetch)
    main = make_main(tmp_path)
    lib = main._library()
    entry = lib.add(url="https://x/cat.png", desc="猫大笑", tags="开心 猫")["entry"]
    assert not entry.get("path")
    result = await main.send_sticker("猫 开心", "onebot11", "12345")
    assert result.startswith("已发送")
    assert calls == ["https://x/cat.png"]
    saved = lib.get(str(entry["id"]))
    assert saved and saved.get("path") and Path(saved["path"]).is_file()
    adapter = main.sdk.adapter.get("onebot11")
    image_ref = next(entry[-1] for entry in adapter.send_log if entry[0] == "Image")
    assert image_ref == saved["path"]


async def test_send_sticker_failed_result_is_reported(tmp_path):
    """平台返回 failed 时不能假装已发送，也不能标记已用。"""
    from fakes import FakeSendChain

    class FailChain(FakeSendChain):
        async def Image(self, ref):
            self._log.append(("Image", ref))
            return {"status": "failed", "message": "retcode 1400"}

    class FailAdapter(FakeAdapter):
        @property
        def Send(self):
            return FailChain(self.send_log)

    main = make_main(tmp_path, adapters={"onebot11": FailAdapter()})
    lib = main._library()
    entry = lib.add(path=str(tmp_path / "a.png"), desc="猫", tags="猫")["entry"]
    Path(entry["path"]).write_bytes(b"x")
    result = await main.send_sticker("猫", "onebot11", "1")
    assert "失败" in result and "1400" in result
    assert (lib.get(str(entry["id"])) or {}).get("uses", 0) == 0
