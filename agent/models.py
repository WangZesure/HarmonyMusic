"""Pydantic models for the tool layer (contract C7)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

MatchType = Literal["keyword", "lyric", "tag", "semantic", "similar", "cf"]
ErrorCode = Literal["INVALID_ARGUMENT", "NOT_FOUND", "TIMEOUT", "UNAVAILABLE", "INTERNAL"]


class DurationRange(BaseModel):
    min: Optional[int] = None
    max: Optional[int] = None


class Filter(BaseModel):
    """Shared by filter_by_tags and semantic_search (C7). Values come from vocab C3."""
    model_config = ConfigDict(extra="forbid")   # wrong keys must fail, not be ignored
    genres: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    vocal: Optional[Literal["vocal", "instrumental"]] = None
    languages: list[str] = Field(default_factory=list)
    duration_s: Optional[DurationRange] = None


class Match(BaseModel):
    type: MatchType
    line_no: Optional[int] = None
    line_text: Optional[str] = None
    start_ms: Optional[int] = None


class SongHit(BaseModel):
    """The compact hit handed to the LLM (C7). No lyric text / url / pic."""
    song_id: int
    name: str
    artist: str
    duration_s: Optional[int] = None
    vocal: Optional[str] = None
    language: Optional[str] = None
    genres: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    rank: int
    score: float = 0.0
    match: Optional[Match] = None


class ToolError(BaseModel):
    code: ErrorCode
    message: str


class ToolResult(BaseModel):
    ok: bool
    data: Optional[dict] = None      # {"items": [SongHit, ...]}
    error: Optional[ToolError] = None

    @classmethod
    def ok_items(cls, items: list[SongHit]) -> "ToolResult":
        return cls(ok=True, data={"items": [i.model_dump() for i in items]}, error=None)

    @classmethod
    def fail(cls, code: ErrorCode, message: str) -> "ToolResult":
        return cls(ok=False, data=None, error=ToolError(code=code, message=message))


# --- per-tool argument models (drive the generated function-calling schema) ---
class KeywordArgs(BaseModel):
    query: str = Field(description="歌名/歌手/专辑关键词，可为中文或英文")
    limit: int = 10
    exclude_ids: list[int] = Field(default_factory=list)


class LyricsArgs(BaseModel):
    query: str = Field(description="一句或半句歌词，可含错别字")
    limit: int = 10
    exclude_ids: list[int] = Field(default_factory=list)


class TagsArgs(BaseModel):
    filters: Filter
    limit: int = 10
    exclude_ids: list[int] = Field(default_factory=list)


class SemanticArgs(BaseModel):
    query: str = Field(description="自然语言描述，如“适合跑步的燃曲”")
    filters: Optional[Filter] = None
    limit: int = 10
    exclude_ids: list[int] = Field(default_factory=list)


class SimilarArgs(BaseModel):
    song_id: int
    limit: int = 10
    exclude_ids: list[int] = Field(default_factory=list)


class ProfileArgs(BaseModel):
    limit: int = 10
    # user_id is injected by the runtime from JWT, never filled by the LLM (C7)


class RecommendArgs(BaseModel):
    limit: int = 10
    candidates: Optional[list[int]] = Field(
        default=None, description="重排模式：给这批候选歌曲重新排序；留空则按用户整体偏好推荐")
    exclude_ids: list[int] = Field(default_factory=list)


class DetailArgs(BaseModel):
    song_id: int
