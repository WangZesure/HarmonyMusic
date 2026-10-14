"""Phase-4 multi-turn tests."""
from __future__ import annotations

from agent.core.chat import classify_turn
from agent.core.session import SessionStore


def test_classify_more():
    assert classify_turn("换一首")[0] == "more"
    assert classify_turn("再来一首")[0] == "more"
    assert classify_turn("其他的歌")[0] == "more"


def test_classify_refine():
    action, tag = classify_turn("更欢快一点")
    assert action == "refine" and tag == ("mood", "开心")
    action, tag = classify_turn("再伤感一点")
    assert action == "refine" and tag == ("mood", "伤感")


def test_classify_negative():
    action, tag = classify_turn("不要伤感")
    assert action == "negative" and tag == ("mood", "伤感")


def test_classify_new():
    assert classify_turn("来点深夜的歌")[0] == "new"


def test_session_store_isolated():
    s = SessionStore()
    a = s.get("a"); b = s.get("b")
    a.seen.append(1); s.save(a)
    assert s.get("a").seen == [1]
    assert s.get("b").seen == []
