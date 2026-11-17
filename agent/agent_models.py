"""Pydantic contracts for the v6 agent tools and output.

Kept separate from agent/models.py so the existing tool layer (phase 2/3)
keeps its C7 models untouched.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.models import Filter


class SearchArgs(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description="自然语言需求，例如“深夜伤感的粤语歌”。只按标签筛选时可省略。")
    mode: Literal["auto", "title", "artist", "lyric", "semantic"] = Field(
        default="auto",
        description="title/artist：点名歌名或歌手；lyric：一句歌词；auto/semantic：自然语言描述。")
    artist: Optional[str] = Field(
        default=None,
        description="用户同时点名歌手时填（如“Coldplay 的 Yellow”里的 Coldplay）；"
                    "mode=title 用来核对版本，歌手不符会直接拒答。")
    filters: Optional[Filter] = Field(
        default=None,
        description="硬约束，只在用户明说时填；值来自 C3 词表。")
    soft_tags: list[str] = Field(default_factory=list, description="只加分、不筛除的软偏好")
    exclude_tags: list[str] = Field(default_factory=list, description="不要这些标签的歌，如“伤感”")
    exclude_song_ids: list[int] = Field(default_factory=list, description="“换一首”时排除已展示的歌")
    limit: int = Field(default=30, ge=1, le=30)
    offset: int = Field(default=0, ge=0, le=30)
    relax: bool = Field(default=False, description="是否允许工具自动放宽约束（默认否，由 agent 决定）")


class RefineArgs(BaseModel):
    drop_tags: list[str] = Field(default_factory=list, description="去掉带这些标签的歌")
    keep_tags: list[str] = Field(default_factory=list, description="只保留至少命中一个这些标签的歌")
    drop_song_ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=30)


class DetailArgs(BaseModel):
    song_id: int


class ProfileArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=20)


class RememberArgs(BaseModel):
    tag: str = Field(min_length=1, max_length=32)
    polarity: Literal["like", "dislike"] = "like"
    weight: float = Field(default=0.1, ge=0.05, le=0.3)


class AskUserArgs(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(default_factory=list, max_length=5)
    slot: str = Field(default="", description="等待用户填写的槽位，如 filters.languages")


class FinishArgs(BaseModel):
    song_ids: list[int] = Field(min_length=1, max_length=5)
    reasons: dict[int, str] = Field(
        default_factory=dict,
        description='可选：{"<song_id>": "一句理由"}；source=template 时忽略')
    message: str = Field(default="为你找到以下歌曲：", max_length=300)
    source: Literal["llm", "template"] = "llm"


class Degraded(BaseModel):
    reasons: list[str] = Field(default_factory=list)
    at_step: int = 0
    partial: bool = False


class AgentOutput(BaseModel):
    """The structured Output of one Run. `type` is the discriminator."""
    type: Literal["songs", "clarify", "refusal", "text"]
    songs: list[dict] = Field(default_factory=list)
    message: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    slot: str = ""
    degraded: Optional[Degraded] = None
    turn_id: str = ""
    session_id: str = ""
    trace: list[dict] = Field(default_factory=list)


AGENT_TOOL_NAMES = [
    "search_songs",
    "refine_candidates",
    "song_detail",
    "user_profile",
    "remember_preference",
    "ask_user",
    "finish",
]
