"""
相对时间解析工具

将自然语言中的相对时间（如 "3天后"、"下周"、"明天"、"2026-08-08"）解析为绝对时间。
供 LLM 解析层（parse_prd / ingest_document）在识别时间敏感语句时使用。

支持格式：
- 绝对时间：ISO 8601、YYYY-MM-DD、YYYY-MM-DD HH:MM:SS
- 相对时间：X天后 / X天后 / X周后 / X月后、明天、后天、下周、下月
- 支持指定时区（默认 Asia/Shanghai，与项目部署时区一致）
"""

import re
from datetime import datetime, timedelta, timezone

# 中文数字映射（支持 1-99）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_num_to_int(text):
    """将中文数字转为 int，支持 1-99（如 "三"->3, "十五"->15, "二十"->20, "二十三"->23）。
    无法解析时返回 None。
    """
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        # 十X -> 10 + X
        rest = text[1:]
        if rest in _CN_DIGITS:
            return 10 + _CN_DIGITS[rest]
        return None
    if "十" in text:
        # X十Y -> X*10 + Y
        parts = text.split("十")
        tens = _CN_DIGITS.get(parts[0], 0)
        ones = _CN_DIGITS.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def parse_human_time(text, now=None, tz=None):
    """把人类可读的时间表达解析为 ISO 8601 字符串。

    Args:
        text: 时间表达（如 "3天后"、"三天后"、"2026-08-08"）
        now: 基准时间（datetime），默认当前时间
        tz: 时区（tzinfo），默认 Asia/Shanghai (+8)

    Returns:
        ISO 8601 字符串；无法解析时返回 None
    """
    if not text:
        return None
    text = str(text).strip()
    if not text:
        return None

    if tz is None:
        tz = timezone(timedelta(hours=8))  # Asia/Shanghai
    if now is None:
        now = datetime.now(tz)

    # 1. 绝对 ISO 8601 / 日期时间
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            # 无时区信息的日期，按目标时区解释
            return dt.replace(tzinfo=tz).isoformat()
        except ValueError:
            continue

    # 2. 相对时间：X天后 / X天后 / X周后 / X月后 / X小时后（支持阿拉伯数字和中文数字）
    m = re.match(r"^\s*(\d+|[零一二两三四五六七八九十]+)\s*(天|日|周|星期|月|年|小时|小时|分钟|秒)后\s*$", text)
    if m:
        num = int(m.group(1)) if m.group(1).isdigit() else _cn_num_to_int(m.group(1))
        if num is None:
            return None
        unit = m.group(2)
        delta_map = {
            "天": timedelta(days=num),
            "日": timedelta(days=num),
            "周": timedelta(weeks=num),
            "星期": timedelta(weeks=num),
            "月": timedelta(days=num * 30),
            "年": timedelta(days=num * 365),
            "小时": timedelta(hours=num),
            "分钟": timedelta(minutes=num),
            "秒": timedelta(seconds=num),
        }
        return (now + delta_map[unit]).isoformat()

    # 3. 相对时间：X天前（历史）
    m = re.match(r"^\s*(\d+|[零一二两三四五六七八九十]+)\s*(天|日|周|月|年|小时|分钟|秒)前\s*$", text)
    if m:
        num = int(m.group(1)) if m.group(1).isdigit() else _cn_num_to_int(m.group(1))
        if num is None:
            return None
        unit = m.group(2)
        delta_map = {
            "天": timedelta(days=num),
            "日": timedelta(days=num),
            "周": timedelta(weeks=num),
            "月": timedelta(days=num * 30),
            "年": timedelta(days=num * 365),
            "小时": timedelta(hours=num),
            "分钟": timedelta(minutes=num),
            "秒": timedelta(seconds=num),
        }
        return (now - delta_map[unit]).isoformat()

    # 4. 常见词
    word_map = {
        "今天": timedelta(days=0),
        "明天": timedelta(days=1),
        "后天": timedelta(days=2),
        "大后天": timedelta(days=3),
        "下周": timedelta(days=7),
        "下周初": timedelta(days=7),
        "下月": timedelta(days=30),
        "明年": timedelta(days=365),
        "昨天": timedelta(days=-1),
        "前天": timedelta(days=-2),
    }
    for word, delta in word_map.items():
        if text.startswith(word):
            return (now + delta).isoformat()

    return None


def extract_time_info(text, now=None):
    """从一段文本中提取时间信息。

    返回 (valid_from, valid_until, matched_text)：
    - valid_from: 解析到的时间（ISO 或 None）
    - valid_until: 未支持（None）
    - matched_text: 匹配到的时间表达片段
    """
    if not text:
        return None, None, None

    # 尝试整体匹配时间表达
    dt = parse_human_time(text, now=now)
    if dt:
        return dt, None, text

    # 在文本中查找形如 "X天后" 的片段（支持阿拉伯数字和中文数字）
    m = re.search(r"((?:\d+|[零一二两三四五六七八九十]+)\s*(?:天|日|周|月|年|小时)后|(?:\d+|[零一二两三四五六七八九十]+)\s*(?:天|日|周|月|年|小时)前|明天|后天|下周|下月|今天|昨天|前天)", text)
    if m:
        matched = m.group(1)
        dt = parse_human_time(matched, now=now)
        return dt, None, matched

    return None, None, None


if __name__ == "__main__":
    tests = ["3天后", "2026-08-08", "明天", "下周", "5天后", "1周后", "2026-08-08 10:00:00", "昨天"]
    for t in tests:
        print(f"{t} -> {parse_human_time(t)}")