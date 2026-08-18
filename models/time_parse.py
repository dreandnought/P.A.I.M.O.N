"""
向后兼容 shim：本文件保留以兼容旧代码（`from models.time_parse import ...`）。

所有时序解析逻辑已迁移至 `models.Istaroth`，请新代码直接 import Istaroth。
"""

from .Istaroth import (
    parse_human_time,
    extract_time_info,
)

__all__ = ["parse_human_time", "extract_time_info"]
