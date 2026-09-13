"""骨架生成与 API 请求字段测试（不依赖 LLM）。"""

from __future__ import annotations


class TestSkeleton:
    def test_skeleton_tokyo_fast(self):
        from app.skeleton import build_skeleton, skeleton_meta
        text = build_skeleton("东京", 3, 1500, "美食,动漫")
        assert "东京" in text
        assert "Day 1" in text
        assert "1500" in text or "$1500" in text or "1500" in text.replace(",", "")
        meta = skeleton_meta("东京", 3, 1500)
        assert meta["known"] is True
        assert meta["destination"] == "东京"

    def test_skeleton_unknown_city_friendly(self):
        from app.skeleton import build_skeleton
        text = build_skeleton("不存在的城市xyz", 2, 500, "随便")
        assert "骨架" in text or "知识库" in text
        assert "不存在的城市xyz" in text or "Agent" in text

    def test_skeleton_not_empty(self):
        from app.skeleton import build_skeleton
        text = build_skeleton("曼谷", 5, 800, "寺庙")
        assert len(text) > 100
        assert "曼谷" in text


class TestTripRequestFields:
    def test_progressive_and_mode_defaults(self):
        from app.models import TripRequest
        req = TripRequest(destination="东京", days=3, budget=1000)
        assert req.progressive is True
        assert req.mode is None
        assert req.enable_reflection is None

    def test_mode_override(self):
        from app.models import TripRequest
        req = TripRequest(destination="东京", days=3, budget=1000, mode="single", progressive=False)
        assert req.mode == "single"
        assert req.progressive is False
