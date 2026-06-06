"""跨 schema 共享的小类型。"""

from __future__ import annotations

from typing import Literal

ThinkingMode = Literal["Lite", "Medium", "Zen"]
MessageIntent = Literal["auto", "explain", "subdivide"]
NextActionKind = Literal["explain", "subdivide"]
