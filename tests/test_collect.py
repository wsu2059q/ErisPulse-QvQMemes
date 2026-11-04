"""Collector 单元测试：观察收录/指纹去重/每日上限/下载降级/视觉打标签。"""

from __future__ import annotations

from pathlib import Path

from fakes import FakeSDK, drain_tasks, make_config

from QvQMemes.collect import Collector, parse_vision_result
from QvQMemes.library import SEEN_CAP, UNTAGGED, StickerLibrary

PNG = b"\x89PNG\r\n\x1a\nfakepngbytes"


def make_collector(sdk=None, handlers=None, library_dir="", **cfg_overrides):
    sdk = sdk or FakeSDK(handlers)
    overrides = {"library_dir": library_dir or "data/QvQMemes/stickers", **cfg_overrides}
    cfg = make_config(**overrides)
    holder = type("H", (), {"cfg": cfg})()
    lib = StickerLibrary(sdk, lambda: holder.cfg)
    col = Collector(sdk, lambda: holder.cfg, lib)
    return col, lib, holder


def file_url(path: Path) -> str:
    return path.as_uri()


class TestParseVisionResult:
    def test_two_lines(self):
        desc, tags = parse_vision_result("一只猫在捧腹大笑\n开心 调侃 猫咪 沙雕")
        assert desc == "一只猫在捧腹大笑"
        assert tags == ["开心", "调侃", "猫咪", "沙雕"]

    def test_empty_and_single_line(self):
        assert parse_vision_result("") == ("", [])
        assert parse_vision_result("\n  \n") == ("", [])
        desc, tags = parse_vision_result("只有一行描述")
        assert desc == "只有一行描述"
        assert tags == []

    def test_label_prefix_stripped_and_capped(self):
        desc, tags = parse_vision_result("描述：猫笑\n标签：a b c d e f g h")
        assert desc == "猫笑"
        assert tags == ["a", "b", "c", "d", "e", "f"]  # 上限 6

    def test_long_desc_truncated(self):
        desc, _ = parse_vision_result("x" * 200 + "\n标签")
        assert len(desc) == 60


class TestObserveCollect:
    async def test_observe_downloads_and_tags(self, tmp_path):
        src = tmp_path / "src.png"
        src.write_bytes(PNG)
        lib_dir = tmp_path / "stickers"
        vision_calls = []

        async def describe(**kw):
            vision_calls.append(kw)
            return "一只猫在笑\n开心 调侃 猫咪"

        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): describe},
            library_dir=str(lib_dir),
        )
        await col.on_observe({"chat_key": "onebot11:group:1", "images": [file_url(src)]})
        await drain_tasks(col)

        assert len(vision_calls) == 1
        assert vision_calls[0]["prompt"]  # 带结构化打标签 prompt
        entries = lib.list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["desc"] == "一只猫在笑"
        assert entry["tags"] == ["开心", "调侃", "猫咪"]
        assert entry["path"].startswith(lib_dir.as_posix())
        assert Path(entry["path"]).read_bytes() == PNG
        assert entry["source"] == "onebot11:group:1"
        # 指纹（url + sha256）已登记
        seen = col._seen_list()
        assert file_url(src) in seen
        assert any(len(fp) == 64 for fp in seen)

    async def test_url_and_sha_dedup(self, tmp_path):
        a = tmp_path / "a.png"
        a.write_bytes(PNG)
        b = tmp_path / "b.png"  # 同内容不同 URL/文件名 → sha256 指纹去重
        b.write_bytes(PNG)
        c = tmp_path / "c.png"
        c.write_bytes(b"different")

        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): "x"},  # 常量返回 → 解析失败 → 未分类
            library_dir=str(tmp_path / "stickers"),
        )
        await col.on_observe({"chat_key": "g", "images": [file_url(a)]})
        await drain_tasks(col)
        await col.on_observe({"chat_key": "g", "images": [file_url(a), file_url(b)]})
        await drain_tasks(col)
        await col.on_observe({"chat_key": "g", "images": [file_url(c)]})
        await drain_tasks(col)

        assert len(lib.list()) == 2  # a(b 同内容去重) + c

    async def test_daily_limit_per_chat(self, tmp_path):
        u1 = tmp_path / "u1.png"
        u1.write_bytes(b"one")
        u2 = tmp_path / "u2.png"
        u2.write_bytes(b"two")
        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): "x"},
            library_dir=str(tmp_path / "stickers"),
            collect_per_chat_daily=1,
        )
        # 同一消息两张不同图：第 1 张入库后达上限，第 2 张跳过
        await col.on_observe({"chat_key": "chatA", "images": [file_url(u1), file_url(u2)]})
        await drain_tasks(col)
        assert len(lib.list()) == 1
        # 次日限额按会话隔离：另一会话仍可收
        await col.on_observe({"chat_key": "chatB", "images": [file_url(u2)]})
        await drain_tasks(col)
        assert len(lib.list()) == 2

    async def test_download_failure_keeps_url(self, tmp_path):
        bad = (tmp_path / "missing.png").as_uri()  # file:// 不存在 → 下载失败
        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): "x"},
            library_dir=str(tmp_path / "stickers"),
        )
        result = await col.collect_url(bad, source="chatX")
        assert result["added"] is True
        entry = result["entry"]
        assert entry["url"] == bad
        assert entry["path"] == ""  # 失败保留 URL 引用
        assert col._is_seen(bad)

    async def test_local_path_collect(self, tmp_path):
        f = tmp_path / "local.png"
        f.write_bytes(PNG)
        col, lib, _ = make_collector(library_dir=str(tmp_path / "stickers"))
        result = await col.collect_url(str(f), source="manual")
        assert result["added"] is True
        assert result["entry"]["path"] == f.as_posix()
        assert result["entry"]["source"] == "manual"

    async def test_auto_collect_off(self, tmp_path):
        f = tmp_path / "x.png"
        f.write_bytes(PNG)
        col, lib, _ = make_collector(
            library_dir=str(tmp_path / "stickers"), auto_collect=False)
        await col.on_observe({"chat_key": "g", "images": [file_url(f)]})
        await drain_tasks(col)
        assert lib.list() == []
        assert not col.tasks

    async def test_observe_defensive_payloads(self):
        col, lib, _ = make_collector()
        for payload in (None, "string", 123, {}, {"images": None},
                        {"chat_key": "g", "images": []},
                        {"chat_key": "g", "images": [None, "  "]}):
            await col.on_observe(payload)  # 绝不抛错
        await drain_tasks(col)
        assert lib.list() == []


class TestVisionTagging:
    async def test_tag_service_missing_stays_untagged(self, tmp_path):
        f = tmp_path / "f.png"
        f.write_bytes(PNG)
        col, lib, _ = make_collector(
            library_dir=str(tmp_path / "stickers"))  # 无 describe_image handler
        result = await col.collect_url(file_url(f), source="g")
        await drain_tasks(col)
        entry = lib.get(result["entry"]["id"])
        assert entry["desc"] == ""
        assert entry["tags"] == [UNTAGGED]
        assert result["added"] is True  # 打标签失败不影响收录

    async def test_tag_via_vision_off(self, tmp_path):
        f = tmp_path / "f.png"
        f.write_bytes(PNG)
        sdk_calls = []

        async def boom(**kw):
            sdk_calls.append(kw)
            raise RuntimeError("should not be called")

        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): boom},
            library_dir=str(tmp_path / "stickers"),
            tag_via_vision=False,
        )
        await col.collect_url(file_url(f), source="g")
        await drain_tasks(col)
        assert sdk_calls == []
        assert lib.list()[0]["tags"] == [UNTAGGED]

    async def test_tag_handler_raises_no_propagation(self, tmp_path):
        f = tmp_path / "f.png"
        f.write_bytes(PNG)

        async def boom(**kw):
            raise RuntimeError("vision down")

        col, lib, _ = make_collector(
            handlers={("QvQChat", "describe_image"): boom},
            library_dir=str(tmp_path / "stickers"),
        )
        await col.collect_url(file_url(f), source="g")  # 不抛
        await drain_tasks(col)
        assert lib.list()[0]["tags"] == [UNTAGGED]


class TestSeenCap:
    def test_seen_fifo_cap(self, tmp_path):
        col, _, _ = make_collector(library_dir=str(tmp_path))
        col._mark_seen([f"fp{i}" for i in range(SEEN_CAP + 10)])
        seen = col._seen_list()
        assert len(seen) == SEEN_CAP
        assert "fp0" not in seen  # 最旧的被挤掉
        assert f"fp{SEEN_CAP + 9}" in seen
