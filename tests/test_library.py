"""StickerLibrary 单元测试：收录幂等/检索打分/新鲜度降权/淘汰/扫描/统计。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from fakes import FakeSDK, make_config

from QvQMemes.library import SEEN_CAP, UNTAGGED, StickerLibrary


def make_library(sdk=None, **cfg_overrides):
    sdk = sdk or FakeSDK()
    cfg = make_config(**cfg_overrides)
    holder = SimpleNamespace(cfg=cfg)
    lib = StickerLibrary(sdk, lambda: holder.cfg)
    return lib, sdk, holder


class TestAdd:
    def test_add_url_and_idempotent(self):
        lib, _, _ = make_library()
        first = lib.add(url="https://x/1.png", desc="开心的猫", tags="开心 猫")
        assert first["added"] is True
        assert first["entry"]["id"] == "m000001"
        assert first["entry"]["tags"] == ["开心", "猫"]
        dup = lib.add(url="https://x/1.png")
        assert dup["added"] is False
        assert dup["entry"]["id"] == "m000001"

    def test_add_requires_path_or_url(self):
        lib, _, _ = make_library()
        assert lib.add()["entry"] is None
        assert lib.add()["error"] == "path or url required"

    def test_tags_normalization(self):
        lib, _, _ = make_library()
        # 字符串多分隔符拆分 + 去重
        e = lib.add(url="u1", tags="开心, 调侃、开心；嘲讽")["entry"]
        assert e["tags"] == ["开心", "调侃", "嘲讽"]
        # 空 → 未分类
        assert lib.add(url="u2")["entry"]["tags"] == [UNTAGGED]
        assert lib.add(url="u3", tags=" ")["entry"]["tags"] == [UNTAGGED]

    def test_memory_fallback_without_storage(self):
        sdk = SimpleNamespace()  # 无 storage 属性
        lib = StickerLibrary(sdk, lambda: make_config())
        lib.add(url="https://x/mem.png", desc="内存兜底")
        assert lib.list()[0]["desc"] == "内存兜底"


class TestSearch:
    def seed(self, lib):
        lib.add(url="a", desc="一只猫大笑", tags="开心 猫咪", now=100)
        lib.add(url="b", desc="狗狗歪头疑惑", tags="疑惑 狗狗", now=200)
        lib.add(url="c", desc="猫咪疑惑地看着你", tags="疑惑 猫咪", now=300)

    def test_tag_exact_beats_desc_only(self):
        lib, _, _ = make_library()
        self.seed(lib)
        hits = lib.search("猫咪", top_n=3)
        # c：标签全等 4 + desc 命中 3 = 7；a：标签全等 4（desc 不含"猫咪"）
        assert [h["id"] for h in hits[:2]] == ["m000003", "m000001"]
        assert hits[0]["score"] == 7.0
        assert hits[1]["score"] == 4.0

    def test_no_match_returns_empty(self):
        lib, _, _ = make_library()
        self.seed(lib)
        assert lib.search("完全不存在") == []
        assert lib.search("") == []

    def test_recency_downweight_is_deterministic(self):
        lib, _, holder = make_library()
        holder.cfg.recency_hours = 72
        self.seed(lib)
        lib.mark_used("m000001", now=time.time())  # a 刚用过
        hits = lib.search("猫咪", top_n=3)
        # a 原分 4.0，窗口内用过降权 ×0.5 = 2.0
        a = next(h for h in hits if h["id"] == "m000001")
        assert a["score"] == 2.0

    def test_recency_window_expired_no_downweight(self):
        lib, _, holder = make_library()
        holder.cfg.recency_hours = 1
        self.seed(lib)
        now = time.time()
        lib.mark_used("m000001", now=now - 2 * 3600)  # 2 小时前用过，窗口 1h 已过
        hits = lib.search("猫咪", top_n=3)
        a = next(h for h in hits if h["id"] == "m000001")
        assert a["score"] == 4.0  # 不降权，保持原分

    def test_mark_used_increments(self):
        lib, _, _ = make_library()
        lib.add(url="a", desc="x")
        assert lib.mark_used("m000001") is True
        assert lib.get("m000001")["uses"] == 1
        assert lib.mark_used("nope") is False


class TestEvictAndScan:
    def test_evict_oldest_used_first(self, tmp_path):
        lib, _, holder = make_library(library_dir=str(tmp_path))
        holder.cfg.max_library = 2
        now = time.time()
        lib.add(url="a", desc="旧且从未用", now=now - 500)
        lib.add(url="b", desc="最近用过", now=now - 400)
        lib.add(url="c", desc="最新", now=now - 300)
        lib.mark_used("m000002", now=now)
        evicted = lib.evict_to_limit()
        assert evicted == 1
        ids = {e["id"] for e in lib.list()}
        assert "m000001" not in ids  # 最久未用被淘汰
        assert ids == {"m000002", "m000003"}

    def test_scan_directory_idempotent(self, tmp_path):
        f1 = tmp_path / "cat.png"
        f1.write_bytes(b"png")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "dog.gif").write_bytes(b"gif")
        (tmp_path / "note.txt").write_text("not image")
        lib, _, _ = make_library(library_dir=str(tmp_path))
        first = lib.scan_directory()
        assert {e["path"] for e in first} == {f1.as_posix(), (sub / "dog.gif").as_posix()}
        assert all(e["source"] == "scan" for e in first)
        assert all(e["tags"] == [UNTAGGED] for e in first)
        assert lib.scan_directory() == []  # 幂等
        assert len(lib.list()) == 2

    def test_scan_missing_directory(self, tmp_path):
        lib, _, _ = make_library(library_dir=str(tmp_path / "nope"))
        assert lib.scan_directory() == []


class TestStatsAndTags:
    def test_stats_and_pending(self):
        lib, _, _ = make_library()
        now = time.time()
        lib.add(url="a", desc="猫笑", tags="开心 猫", now=now)
        lib.add(url="b", tags="", now=now)  # 未分类
        lib.mark_used("m000001")
        stats = lib.stats()
        assert stats["total"] == 2
        assert stats["untagged"] == 1
        assert stats["added_today"] == 2
        assert stats["tag_distribution"]["开心"] == 1
        assert stats["top_used"][0]["id"] == "m000001"
        assert lib.pending_tag_ids() == ["m000002"]

    def test_tag_cloud_excludes_untagged(self):
        lib, _, _ = make_library()
        lib.add(url="a", tags="开心 猫")
        lib.add(url="b", tags="开心")
        lib.add(url="c", tags="")
        assert lib.tag_cloud(5) == ["开心", "猫"]

    def test_seen_cap_exported(self):
        assert SEEN_CAP == 5000
