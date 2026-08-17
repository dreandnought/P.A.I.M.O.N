"""
LLM 驱动的 PRD/文档解析器。

职责:
1. 从文本/Markdown 中抽取实体和关系
2. 将实体与预设 Ontology 进行语义匹配
3. 根据自然语言描述生成本体变更计划
4. 生成增强版 PRD(四阶段流水线:抽取 → 匹配+图搜索 → 推理 → 融合)

注意:当前为验证阶段,不引入向量模型,使用纯 LLM 语义匹配。
"""

import json
import os
import re
import time
import yaml
from typing import Optional
from concurrent.futures import ThreadPoolExecutor


# 从 llm_config.yaml 加载 LLM 配置
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "llm_config.yaml")


def _load_llm_config():
    """加载 llm_config.yaml 配置(每次调用都重新读取,支持热更新)。"""
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        provider_name = cfg.get("default_provider", "siliconflow")
        provider = cfg.get("providers", {}).get(provider_name, {})
        parser_cfg = cfg.get("prd_parser", {})
        return {
            "api_key": provider.get("api_key", ""),
            "base_url": provider.get("base_url", ""),
            "model": provider.get("models", {}).get("chat", ""),
            "temperature": parser_cfg.get("temperature", 0.1),
            "max_tokens": parser_cfg.get("max_tokens", 4096),
        }
    return None


def _chat_url(base_url: str) -> str:
    """根据 base_url 构造 chat completions URL,保留用户指定的显式路径。"""
    if not base_url:
        return base_url
    from urllib.parse import urlparse

    path = urlparse(base_url).path.rstrip("/")
    if path.endswith("/chat/completions"):
        return base_url
    return base_url.rstrip("/") + "/chat/completions"


def _call_llm(system_prompt: str, user_prompt: str, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> Optional[str]:
    """调用 LLM API(每次调用都重新读取配置)。"""
    import requests

    cfg = _load_llm_config()
    if cfg:
        api_url = _chat_url(cfg["base_url"])
        api_key = cfg["api_key"]
        model = cfg["model"]
        temp = temperature if temperature is not None else cfg.get("temperature", 0.1)
        max_tok = max_tokens if max_tokens is not None else cfg.get("max_tokens", 4096)
    else:
        api_url = os.environ.get(
            "LLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        )
        api_key = os.environ.get("ARK_API_KEY", os.environ.get("LLM_API_KEY", ""))
        model = os.environ.get("ARK_MODEL_ID", os.environ.get("LLM_MODEL", "doubao-seed-2.0-code"))
        temp = temperature if temperature is not None else 0.1
        max_tok = max_tokens if max_tokens is not None else 4096

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temp,
        "max_tokens": max_tok,
    }

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"LLM API call failed: {e}")


def _build_ontology_context(db_path=None, include_schema=False) -> str:
    """构建 Ontology 上下文字符串(供 LLM 匹配使用)。

    Args:
        db_path: 数据库路径
        include_schema: 是否包含 Schema 层信息（类型层次、关系语义）
    """
    from models.schema import get_connection

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT e.id, e.name, et.name AS type_name, e.description "
            "FROM entities e JOIN entity_types et ON e.type_id = et.id "
            "WHERE e.status = 'active' "
            "ORDER BY e.type_id, e.name"
        ).fetchall()

        if not rows:
            return "(当前 Ontology 中暂无实体)"

        lines = []
        for r in rows:
            desc = r["description"] or ""
            lines.append(f"- {r['id']} | {r['name']} ({r['type_name']}) | {desc[:80]}")

        entity_text = "\n".join(lines)

        if include_schema:
            # 追加 Schema 层信息
            schema_lines = ["\n### Schema 层信息"]

            # 实体类型层次
            et_rows = conn.execute(
                """SELECT et.id, et.name, et.description, et.parent_id,
                          (SELECT name FROM entity_types WHERE id = et.parent_id) AS parent_name
                   FROM entity_types et ORDER BY et.id"""
            ).fetchall()
            schema_lines.append("\n实体类型层次:")
            for r in et_rows:
                parent_str = f" → {r['parent_name']}" if r['parent_name'] else " (根)"
                schema_lines.append(f"- {r['id']} | {r['name']}{parent_str}")

            # 关系类型语义
            rt_rows = conn.execute(
                """SELECT id, name, symmetric, transitive, domain_type, range_type
                   FROM relation_types ORDER BY id"""
            ).fetchall()
            schema_lines.append("\n关系类型语义:")
            for r in rt_rows:
                props = []
                if r['symmetric']:
                    props.append("对称")
                if r['transitive']:
                    props.append("传递")
                if r['domain_type']:
                    props.append(f"domain={r['domain_type']}")
                if r['range_type']:
                    props.append(f"range={r['range_type']}")
                prop_str = f" ({', '.join(props)})" if props else ""
                schema_lines.append(f"- {r['id']}{prop_str}")

            return entity_text + "\n" + "\n".join(schema_lines)

        return entity_text
    finally:
        conn.close()


def _extract_json_blocks(text: str) -> list:
    """从文本中提取所有 ```json ... ``` 代码块内容。"""
    return re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)


def _first_json_block(text: str):
    """提取并解析第一个 JSON 代码块。"""
    blocks = _extract_json_blocks(text)
    if blocks:
        try:
            return json.loads(blocks[0])
        except json.JSONDecodeError:
            pass
    # 兜底:尝试直接解析整个文本中的 JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def _now_utc_iso():
    """返回当前 UTC 时间的 ISO 8601 字符串（供 prompt 注入）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _normalize_time_to_utc(text):
    """将时间表达转为 UTC ISO 8601 字符串。

    优先尝试直接解析为 ISO 8601（LLM 直接输出此格式）；
    若失败，回退到 parse_human_time 解析自然语言（如 "3天后"、"三天后"）。
    两者均失败时返回 None。
    """
    if not text:
        return None
    from datetime import datetime, timezone

    raw = str(text).strip()
    if not raw:
        return None

    # 1. 优先尝试 ISO 8601 解析（LLM 直接输出的格式）
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass

    # 2. 回退：自然语言解析（parse_human_time 支持中文数字）
    from models.time_parse import parse_human_time
    dt_str = parse_human_time(raw)
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def _scan_time_near_entity(content, entity_name, window=40):
    """在原文中查找实体名附近的时间表达（回退用）。

    在 content 中定位 entity_name 的位置，取前后 window 字符的片段，
    用 extract_time_info 扫描时间表达。找到则返回时间表达原文，否则返回 None。
    """
    if not content or not entity_name:
        return None
    idx = content.find(entity_name)
    if idx < 0:
        return None
    start = max(0, idx - window)
    end = min(len(content), idx + len(entity_name) + window)
    snippet = content[start:end]
    from models.time_parse import extract_time_info
    _, _, matched = extract_time_info(snippet)
    return matched


def extract_entities_and_relations(content: str, db_path=None) -> dict:
    """从文本/Markdown 中抽取实体和关系,并尝试与已有 Ontology 实体匹配。

    返回结构:
    {
        "entities": [
            {
                "name": "用户登录",
                "type": "function",
                "description": "...",
                "suggested_id": "func:user_login",
                "matched_entity_id": null,
                "confidence": 0.0,
                "is_new": true
            }
        ],
        "relations": [
            {
                "source_name": "用户登录",
                "source_id": "func:user_login",
                "relation_type": "depends_on",
                "target_name": "短信验证码",
                "target_id": "iface:sms_service",
                "description": "...",
                "confidence": 0.9
            }
        ]
    }
    """
    ontology_context = _build_ontology_context(db_path, include_schema=True)

    system_prompt = """你是一个本体知识抽取专家。你的任务是从用户提供的文本或 Markdown 文档中抽取功能级实体和它们之间的关系,并与已有 Ontology 进行匹配。

请严格按以下 JSON 格式返回结果(只返回一个 JSON 代码块,不要额外解释):

```json
{
  "entities": [
    {
      "name": "用户登录",
      "type": "function",
      "description": "用户通过手机号或邮箱登录系统",
      "suggested_id": "func:user_login",
      "matched_entity_id": null,
      "confidence": 0.0,
      "is_new": true,
      "available_from": null
    }
  ],
  "relations": [
    {
      "source_name": "用户登录",
      "source_id": "func:user_login",
      "relation_type": "depends_on",
      "target_name": "短信验证码",
      "target_id": "iface:sms_service",
      "description": "登录需要短信验证码",
      "confidence": 0.9
    }
  ],
  "schema": {
    "entity_types": {
      "create": [],
      "update": []
    },
    "relation_types": {
      "create": [],
      "update": []
    }
  }
}
```

规则:
1. 实体类型仅限:requirement、function、module、interface、data_entity、test_case、constraint、actor。
2. suggested_id 规范:使用"类型前缀:英文小写slug",例如 function→func:xxx、module→mod:xxx、interface→iface:xxx、data_entity→data:xxx、actor→actor:xxx、requirement→req:xxx、constraint→constraint:xxx、test_case→test:xxx。
3. 若某实体与"当前 Ontology 中的已知实体"明显是同一个,则填写 matched_entity_id 为已有实体的 id,is_new=false,confidence 为 0.5-1.0;否则 matched_entity_id 为 null,is_new=true。
4. relation_type 仅限:depends_on、causes、constrains、impacts、conflicts_with、derived_from、implements、contains、refines、relates_to。
5. 关系两端的 source_id/target_id 优先使用已有实体的 ID;若目标为新实体,则使用 suggested_id。
6. 只抽取文本中明确提到、有较高置信度的实体和关系,不要过度推断。
7. available_from: 当原文中提到该实体在未来某个时间点生效或发布(如"3天后发布"、"三天后将会发布"、"2026年Q3上线"、"下周生效")时,
   你必须根据上方提供的"当前时间(UTC)"计算出绝对的 ISO 8601 UTC 时间戳,并填入 available_from 字段。
   例如:当前时间是 2026-08-06T12:00:00+00:00,原文说"3天后发布",则 available_from 填 "2026-08-09T12:00:00+00:00"。
   没有时间表达时填 null。该字段表示实体的生效时间,晚于当前时间时实体为"未来实体",其关系将自动成为虚关系。
   重要:请仔细检查原文中是否有与实体相关的时间表达,不要遗漏。输出必须是 ISO 8601 格式,不要输出自然语言。

8. [可选] Schema 分析:如果在文档中发现了现有类型无法覆盖的概念,或者发现现有关系语义需要调整,
   请在 schema 字段中提出变更建议。只在有把握时才写,不确定的不写。
   - entity_types.create: 新增实体类型(需要 id, name, description, parent_id)
   - entity_types.update: 修改现有实体类型(需要 id, parent_id 等)
   - relation_types.create: 新增关系类型(需要 id, name, symmetric, transitive, domain_type, range_type)
   - relation_types.update: 修改现有关系类型(需要 id, 和要修改的字段)
   如果不需要 Schema 变更,返回空对象。"""

    user_prompt = f"""## 当前时间（UTC）
{_now_utc_iso()}

## 当前 Ontology 中的已知实体
{ontology_context}

## 待分析的文本/文档
{content}

请抽取实体和关系,并返回严格 JSON。如有 Schema 变更建议请填入 schema 字段。"""

    llm_response = _call_llm(system_prompt, user_prompt, temperature=0.1, max_tokens=4096)
    parsed = _first_json_block(llm_response)

    if not isinstance(parsed, dict):
        raise RuntimeError("LLM 返回的 JSON 格式不正确,无法解析实体和关系")

    entities = parsed.get("entities", []) if isinstance(parsed.get("entities"), list) else []
    relations = parsed.get("relations", []) if isinstance(parsed.get("relations"), list) else []

    # 基本校验与清洗
    valid_types = {"requirement", "function", "module", "interface", "data_entity", "test_case", "constraint", "actor"}
    cleaned_entities = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        etype = e.get("type", "")
        if etype not in valid_types:
            continue
        cleaned_entities.append({
            "name": str(e.get("name", "")).strip(),
            "type": etype,
            "description": str(e.get("description", "")).strip(),
            "suggested_id": str(e.get("suggested_id", "")).strip() or None,
            "matched_entity_id": e.get("matched_entity_id") or None,
            "confidence": float(e.get("confidence", 0.8)),
            "is_new": bool(e.get("is_new", True)),
            "available_from": e.get("available_from"),
        })

    valid_relation_types = {"depends_on", "causes", "constrains", "impacts", "conflicts_with", "derived_from", "implements", "contains", "refines", "relates_to"}
    cleaned_relations = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        rtype = r.get("relation_type", "")
        if rtype not in valid_relation_types:
            continue
        cleaned_relations.append({
            "source_name": str(r.get("source_name", "")).strip(),
            "source_id": str(r.get("source_id", "")).strip() or None,
            "relation_type": rtype,
            "target_name": str(r.get("target_name", "")).strip(),
            "target_id": str(r.get("target_id", "")).strip() or None,
            "description": str(r.get("description", "")).strip(),
            "confidence": float(r.get("confidence", 0.8)),
        })

    # 提取 schema 变更部分（LLM 可能返回 schema 建议）
    schema = parsed.get("schema", {})
    if not isinstance(schema, dict):
        schema = {}
    schema_entity_types = schema.get("entity_types", {}) if isinstance(schema.get("entity_types"), dict) else {}
    schema_relation_types = schema.get("relation_types", {}) if isinstance(schema.get("relation_types"), dict) else {}

    # 后处理：校验/归一化 available_from 为 UTC ISO 8601
    # LLM 应直接输出 ISO 8601；若输出自然语言则回退到 parse_human_time
    # 若 LLM 未提取 available_from，尝试从原文中扫描实体名附近的时间表达
    for e in cleaned_entities:
        raw_af = e.get("available_from")
        if raw_af:
            e["available_from"] = _normalize_time_to_utc(raw_af)
        else:
            # 回退：在原文中查找实体名附近的时间表达
            fallback_af = _scan_time_near_entity(content, e.get("name", ""))
            e["available_from"] = _normalize_time_to_utc(fallback_af) if fallback_af else None

    return {
        "entities": cleaned_entities,
        "relations": cleaned_relations,
        "schema": {
            "entity_types": {
                "create": schema_entity_types.get("create", []),
                "update": schema_entity_types.get("update", []),
            },
            "relation_types": {
                "create": schema_relation_types.get("create", []),
                "update": schema_relation_types.get("update", []),
            },
        },
    }


def plan_ontology_changes(
    description: str,
    target_entity_id: Optional[str] = None,
    target_entity_context: Optional[str] = None,
    db_path=None,
) -> dict:
    """根据自然语言描述生成本体变更计划。

    返回结构:
    {
        "entities": {"create": [...], "update": [...], "delete": [...]},
        "relations": {"create": [...], "update": [...], "delete": [...]},
        "explanation": "..."
    }
    """
    ontology_context = _build_ontology_context(db_path, include_schema=True)

    system_prompt = """你是一个本体知识库维护专家。用户会用自然语言描述他希望对 Ontology 进行的修改,你的任务是生成一份结构化的变更计划。

请严格按以下 JSON 格式返回结果(只返回一个 JSON 代码块,不要额外解释):

```json
{
  "entities": {
    "create": [
      {"suggested_id": "func:wechat_login", "name": "微信登录", "type": "function", "description": "...", "available_from": null}
    ],
    "update": [
      {"entity_id": "func:user_login", "name": "用户登录", "description": "更新后的描述"}
    ],
    "delete": [
      {"entity_id": "func:old_feature"}
    ]
  },
  "relations": {
    "create": [
      {"source_id": "func:user_login", "relation_type": "depends_on", "target_id": "iface:sms_service", "description": "..."}
    ],
    "update": [
      {"relation_id": "depends_on:func:user_login->iface:sms_service", "description": "..."}
    ],
    "delete": [
      {"relation_id": "..."}
    ]
  },
  "schema": {
    "entity_types": {
      "create": [],
      "update": []
    },
    "relation_types": {
      "create": [],
      "update": []
    }
  },
  "explanation": "简要说明变更原因"
}
```

规则:
1. 实体类型仅限:requirement、function、module、interface、data_entity、test_case、constraint、actor。
2. 新增实体的 suggested_id 使用"类型前缀:英文小写slug"格式。
3. relation_type 仅限:depends_on、causes、constrains、impacts、conflicts_with、derived_from、implements、contains、refines、relates_to。
4. 若用户描述不够明确,宁可少改也不要过度推断;delete 操作要特别谨慎。
5. 若未提供目标实体 ID,由你自行判断要修改哪些实体。
6. available_from: 当用户描述中提到实体在未来某个时间点生效或发布(如"3天后上线"、"三天后发布"、"下周生效")时,
   你必须根据上方提供的"当前时间(UTC)"计算出绝对的 ISO 8601 UTC 时间戳,并填入 available_from 字段。
   例如:当前时间是 2026-08-06T12:00:00+00:00,描述说"3天后上线",则 available_from 填 "2026-08-09T12:00:00+00:00"。
   没有时间表达时填 null。该字段表示实体的生效时间,晚于当前时间时实体为"未来实体",其关系将自动成为虚关系。
   重要:请仔细检查描述中是否有与实体相关的时间表达,不要遗漏。输出必须是 ISO 8601 格式,不要输出自然语言。

7. [可选] Schema 层变更:如果用户要求修改类型层次(如"把function设为requirement的子类型")
   或关系语义(如"新增一个triggers关系类型"),请在 schema 字段中填写。
   只在有把握时才写,不确定的不写。
   - entity_types.create: 新增实体类型(需要 id, name, description, parent_id)
   - entity_types.update: 修改现有实体类型(需要 id, 和要修改的字段)
   - relation_types.create: 新增关系类型(需要 id, name, symmetric, transitive, domain_type, range_type)
   - relation_types.update: 修改现有关系类型(需要 id, 和要修改的字段)
   如果不需要 Schema 变更,返回空对象。"""

    target_hint = ""
    if target_entity_id:
        target_hint = f"\n优先修改的目标实体 ID:{target_entity_id}\n"
    if target_entity_context:
        target_hint += f"目标实体上下文:\n{target_entity_context}\n"

    user_prompt = f"""## 当前时间（UTC）
{_now_utc_iso()}

## 当前 Ontology 中的已知实体
{ontology_context}
{target_hint}
## 用户的修改描述
{description}

请生成变更计划,返回严格 JSON。"""

    llm_response = _call_llm(system_prompt, user_prompt, temperature=0.2, max_tokens=4096)
    parsed = _first_json_block(llm_response)

    if not isinstance(parsed, dict):
        raise RuntimeError("LLM 返回的 JSON 格式不正确,无法解析变更计划")

    # 规范化结构
    entities = parsed.get("entities", {})
    relations = parsed.get("relations", {})
    if not isinstance(entities, dict):
        entities = {}
    if not isinstance(relations, dict):
        relations = {}

    # 提取 schema 变更部分
    schema = parsed.get("schema", {})
    if not isinstance(schema, dict):
        schema = {}

    # 后处理：校验/归一化 available_from 为 UTC ISO 8601
    # LLM 应直接输出 ISO 8601；若输出自然语言则回退到 parse_human_time
    # 若 LLM 未提取 available_from，尝试从描述中扫描实体名附近的时间表达
    entity_create_list = entities.get("create", []) if isinstance(entities.get("create"), list) else []
    for e in entity_create_list:
        if isinstance(e, dict):
            raw_af = e.get("available_from")
            if raw_af:
                e["available_from"] = _normalize_time_to_utc(raw_af)
            else:
                fallback_af = _scan_time_near_entity(description, e.get("name", ""))
                e["available_from"] = _normalize_time_to_utc(fallback_af) if fallback_af else None

    return {
        "entities": {
            "create": entity_create_list,
            "update": entities.get("update", []) if isinstance(entities.get("update"), list) else [],
            "delete": entities.get("delete", []) if isinstance(entities.get("delete"), list) else [],
        },
        "relations": {
            "create": relations.get("create", []) if isinstance(relations.get("create"), list) else [],
            "update": relations.get("update", []) if isinstance(relations.get("update"), list) else [],
            "delete": relations.get("delete", []) if isinstance(relations.get("delete"), list) else [],
        },
        "schema": {
            "entity_types": schema.get("entity_types", {"create": [], "update": []}),
            "relation_types": schema.get("relation_types", {"create": [], "update": []}),
        },
        "explanation": str(parsed.get("explanation", "")).strip(),
    }


# ---------------------------------------------------------------------------
# PRD 增强流水线(四阶段:抽取 → 匹配+图搜索 → 推理 → 融合)
# ---------------------------------------------------------------------------

def _load_prd_parser_config() -> dict:
    """从 llm_config.yaml 的 prd_parser 段读取配置。"""
    defaults = {
        "temperature": 0.1,
        "max_retries": 3,
        "entity_confidence_threshold": 0.5,
        "relation_confidence_threshold": 0.6,
    }
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        parser_cfg = cfg.get("prd_parser", {}) if cfg else {}
        defaults["temperature"] = parser_cfg.get("temperature", defaults["temperature"])
        defaults["max_retries"] = parser_cfg.get("max_retries", defaults["max_retries"])
        extraction = parser_cfg.get("extraction", {})
        defaults["entity_confidence_threshold"] = extraction.get(
            "entity_confidence_threshold", defaults["entity_confidence_threshold"]
        )
        defaults["relation_confidence_threshold"] = extraction.get(
            "relation_confidence_threshold", defaults["relation_confidence_threshold"]
        )
    return defaults


def _load_prd_generator_config() -> dict:
    """从 llm_config.yaml 的 prd_generator 段读取配置。"""
    defaults = {"temperature": 0.7, "max_tokens": 8192}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        gen_cfg = cfg.get("prd_generator", {}) if cfg else {}
        defaults["temperature"] = gen_cfg.get("temperature", defaults["temperature"])
        defaults["max_tokens"] = gen_cfg.get("max_tokens", defaults["max_tokens"])
    return defaults


def _call_llm_with_retry(
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
) -> str:
    """带指数退避重试的 LLM 调用。"""
    last_err = None
    for attempt in range(max_retries):
        try:
            return _call_llm(system_prompt, user_prompt, temperature, max_tokens)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                time.sleep(wait)
    raise RuntimeError(f"LLM 调用失败(重试 {max_retries} 次): {last_err}")


def _extract_prd_entities(content: str) -> list:
    """阶段 1:用 LLM 从 PRD 文本中抽取功能级实体。

    返回 [{name, type, description, search_keywords: [str]}]
    """
    parser_cfg = _load_prd_parser_config()

    system_prompt = """你是实体抽取专家。从用户提供的 PRD 文档中抽取功能级实体。

只返回一个 JSON 代码块,格式如下:

```json
[
  {
    "name": "用户登录",
    "type": "function",
    "description": "用户通过手机号或邮箱登录系统",
    "search_keywords": ["用户登录", "登录", "login"]
  }
]
```

规则:
1. 实体类型仅限:requirement、function、module、interface、data_entity、test_case、constraint、actor
2. search_keywords 是用于在本体中检索的关键词列表(中英文均可),应包含实体的核心名称和同义词
3. 只抽取 PRD 中明确提到的实体,不要过度推断
4. 每个实体至少提供 2 个 search_keywords"""

    user_prompt = f"""## PRD 文档内容
{content}

请抽取功能级实体,返回严格 JSON。"""

    resp = _call_llm_with_retry(
        system_prompt, user_prompt,
        temperature=parser_cfg["temperature"],
        max_retries=parser_cfg["max_retries"],
    )
    parsed = _first_json_block(resp)
    if not isinstance(parsed, list):
        return []

    valid_types = {"requirement", "function", "module", "interface", "data_entity", "test_case", "constraint", "actor"}
    result = []
    for e in parsed:
        if not isinstance(e, dict):
            continue
        etype = e.get("type", "")
        if etype not in valid_types:
            continue
        kws = e.get("search_keywords", [])
        if not isinstance(kws, list):
            kws = [str(kws)]
        result.append({
            "name": str(e.get("name", "")).strip(),
            "type": etype,
            "description": str(e.get("description", "")).strip(),
            "search_keywords": [str(k).strip() for k in kws if str(k).strip()],
        })
    return result


def _semantic_match_entities(prd_entities: list, db_path=None) -> list:
    """阶段 2a+2b:SQL LIKE 预筛选候选 → LLM 批量语义匹配。

    返回 [{prd_entity_name, matched_entity_id, confidence, match: bool}]
    """
    if not prd_entities:
        return []

    from models.entity import search_entities

    parser_cfg = _load_prd_parser_config()
    threshold = parser_cfg["entity_confidence_threshold"]

    # Step 2a: 对每个 PRD 实体,用 search_keywords 做 LIKE 检索获取候选
    candidates_map = {}  # prd_entity_name -> [candidate entity dicts]
    for pe in prd_entities:
        seen_ids = set()
        candidates = []
        for kw in pe.get("search_keywords", [pe["name"]]):
            for hit in search_entities(kw, limit=5, db_path=db_path):
                if hit["id"] not in seen_ids:
                    seen_ids.add(hit["id"])
                    candidates.append({
                        "id": hit["id"],
                        "name": hit["name"],
                        "type": hit.get("type_name", ""),
                        "description": (hit.get("description") or "")[:120],
                    })
        candidates_map[pe["name"]] = candidates

    # 如果所有候选都为空,直接返回未匹配
    if not any(candidates_map.values()):
        return [{"prd_entity_name": pe["name"], "matched_entity_id": None, "confidence": 0.0, "match": False}
                for pe in prd_entities]

    # Step 2b: 一次 LLM 调用批量匹配
    match_input = []
    for pe in prd_entities:
        cands = candidates_map.get(pe["name"], [])
        match_input.append({
            "prd_entity": {"name": pe["name"], "type": pe["type"], "description": pe["description"]},
            "candidates": cands,
        })

    system_prompt = """你是实体语义匹配专家。对于每个 PRD 实体,判断候选实体列表中是否有语义匹配的实体。

只返回一个 JSON 代码块,格式如下:

```json
[
  {
    "prd_entity_name": "用户登录",
    "matched_entity_id": "func:user_login",
    "confidence": 0.95,
    "match": true
  }
]
```

规则:
1. 如果候选列表中有语义匹配的实体,填写 matched_entity_id 和 confidence(0.5-1.0),match=true
2. 如果没有匹配的,matched_entity_id 为 null,confidence 为 0.0,match=false
3. 匹配判断要考虑:名称相似度、类型一致性、描述语义相似度
4. confidence < 0.5 的视为不匹配"""

    user_prompt = f"""## 待匹配的 PRD 实体与候选列表
{json.dumps(match_input, ensure_ascii=False, indent=2)}

请逐一判断匹配关系,返回严格 JSON。"""

    resp = _call_llm_with_retry(
        system_prompt, user_prompt,
        temperature=parser_cfg["temperature"],
        max_retries=parser_cfg["max_retries"],
    )
    parsed = _first_json_block(resp)
    if not isinstance(parsed, list):
        # 兜底:全部标记为不匹配
        return [{"prd_entity_name": pe["name"], "matched_entity_id": None, "confidence": 0.0, "match": False}
                for pe in prd_entities]

    # 清洗 + 应用置信度阈值
    result = []
    for m in parsed:
        if not isinstance(m, dict):
            continue
        conf = float(m.get("confidence", 0.0))
        is_match = bool(m.get("match", False)) and conf >= threshold
        result.append({
            "prd_entity_name": str(m.get("prd_entity_name", "")).strip(),
            "matched_entity_id": m.get("matched_entity_id") if is_match else None,
            "confidence": conf,
            "match": is_match,
        })
    return result


def _graph_search(matched_entity_ids: list, db_path=None, max_depth: int = 2) -> dict:
    """阶段 2c:对匹配到的实体做图搜索,收集子图。

    返回 {entities: [...], relations: [...]}
    """
    from models.relation import get_entity_relations, get_transitive_relations
    from models.entity import get_entities_by_ids

    if not matched_entity_ids:
        return {"entities": [], "relations": []}

    entity_ids_set = set()
    relations_list = []
    seen_relation_ids = set()

    transitive_types = ["depends_on", "contains", "derived_from"]

    for eid in matched_entity_ids:
        entity_ids_set.add(eid)

        # 1跳关系(全部类型)
        for rel in get_entity_relations(eid, db_path=db_path):
            rid = rel.get("id", "")
            if rid not in seen_relation_ids:
                seen_relation_ids.add(rid)
                relations_list.append(rel)
            # 收集关系对端实体
            related_id = rel.get("related_entity_id", "")
            if related_id:
                entity_ids_set.add(related_id)

        # 多跳 BFS(仅可传递关系类型)
        for rtype in transitive_types:
            try:
                for t_rel in get_transitive_relations(eid, relation_type=rtype, max_depth=max_depth, db_path=db_path):
                    entity_ids_set.add(t_rel["entity_id"])
                    # 构造简化的关系表示
                    relation_key = f"{eid}--{rtype}(depth={t_rel['depth']})-->{t_rel['entity_id']}"
                    if relation_key not in seen_relation_ids:
                        seen_relation_ids.add(relation_key)
                        relations_list.append({
                            "id": relation_key,
                            "relation_type": rtype,
                            "source_entity_id": eid,
                            "target_entity_id": t_rel["entity_id"],
                            "target_entity_name": t_rel["entity_name"],
                            "target_entity_type": t_rel["entity_type"],
                            "depth": t_rel["depth"],
                            "transitive": True,
                        })
            except Exception:
                pass  # 某些关系类型可能不存在,跳过

    # 批量获取实体详情
    all_ids = list(entity_ids_set)
    entities = get_entities_by_ids(all_ids, db_path=db_path) if all_ids else []

    return {
        "entities": [{"id": e["id"], "name": e["name"], "type": e.get("type_name", ""), "description": e.get("description", "")}
                      for e in entities],
        "relations": relations_list,
    }


def _format_subgraph_for_llm(subgraph: dict) -> str:
    """将子图格式化为 LLM 可读的文本。"""
    lines = ["## 本体子图(匹配到的实体和关系)"]

    lines.append("\n### 实体")
    for e in subgraph.get("entities", []):
        lines.append(f"- {e['id']} | {e['name']} ({e.get('type', '')}) | {e.get('description', '')[:100]}")

    lines.append("\n### 关系")
    for r in subgraph.get("relations", []):
        if r.get("transitive"):
            lines.append(f"- {r.get('source_entity_id', '?')} --{r['relation_type']}(depth={r.get('depth',1)})--> {r.get('target_entity_name', '?')}")
        else:
            direction = r.get("direction", "")
            rel_type = r.get("relation_type", "")
            related = r.get("related_entity_name", r.get("target_entity_name", "?"))
            lines.append(f"- [{direction}] {rel_type} → {related} (conf: {r.get('confidence', 0)})")

    return "\n".join(lines) if len(lines) > 2 else "(子图为空)"


def _reason_inferences(content: str, subgraph: dict) -> dict:
    """阶段 3:用 3 个并行 LLM subagent 推理隐含的依赖、约束、影响。

    返回 {dependencies: [...], constraints: [...], impacts: [...]}
    """
    parser_cfg = _load_prd_parser_config()
    subgraph_text = _format_subgraph_for_llm(subgraph)

    if not subgraph.get("entities"):
        return {"dependencies": [], "constraints": [], "impacts": []}

    def _run_subagent(role: str, system: str, user: str) -> list:
        try:
            resp = _call_llm_with_retry(
                system, user,
                temperature=parser_cfg["temperature"],
                max_retries=parser_cfg["max_retries"],
            )
            parsed = _first_json_block(resp)
            if isinstance(parsed, dict):
                return parsed.get("inferences", [])
            if isinstance(parsed, list):
                return parsed
            return []
        except Exception:
            return []

    # 三个 subagent 的 prompt
    common_user = f"""## PRD 文档内容
{content}

{subgraph_text}

请基于以上信息进行推理,返回严格 JSON。"""

    dep_system = """你是依赖分析专家。基于 PRD 文档和本体子图,推理出 PRD 中**隐含的**依赖关系(即 PRD 未明确写出,但通过本体关系图可以推断出的依赖)。

只返回一个 JSON 代码块:

```json
{
  "inferences": [
    {
      "source_entity": "Linux屏幕截图",
      "dependency": "Computer Use 模块",
      "evidence": "子图显示 screen_control depends_on computer_use 模块",
      "confidence": 0.85
    }
  ]
}
```"""

    constraint_system = """你是约束分析专家。基于 PRD 文档和本体子图,推理出 PRD 中**隐含的**约束条件(即 PRD 未明确写出,但通过本体关系图可以推断出的约束)。

只返回一个 JSON 代码块:

```json
{
  "inferences": [
    {
      "entity": "Linux键鼠模拟",
      "constraint": "必须支持 X11 和 Wayland 两种协议",
      "evidence": "子图显示 screen_control constrains_by 显示协议兼容性",
      "confidence": 0.80
    }
  ]
}
```"""

    impact_system = """你是影响分析专家。基于 PRD 文档和本体子图,推理出 PRD 中描述的变更会**影响到**哪些已有模块或功能(即通过本体关系图可以推断出的连锁影响)。

只返回一个 JSON 代码块:

```json
{
  "inferences": [
    {
      "entity": "Linux屏幕截图",
      "impacted_module": "MCP 服务器",
      "evidence": "子图显示 computer_use impacts mcp_system",
      "confidence": 0.90
    }
  ]
}
```"""

    # 并行调用 3 个 subagent
    with ThreadPoolExecutor(max_workers=3) as executor:
        fut_dep = executor.submit(_run_subagent, "dependency", dep_system, common_user)
        fut_con = executor.submit(_run_subagent, "constraint", constraint_system, common_user)
        fut_imp = executor.submit(_run_subagent, "impact", impact_system, common_user)

        dependencies = fut_dep.result()
        constraints = fut_con.result()
        impacts = fut_imp.result()

    return {
        "dependencies": dependencies if isinstance(dependencies, list) else [],
        "constraints": constraints if isinstance(constraints, list) else [],
        "impacts": impacts if isinstance(impacts, list) else [],
    }


def _fuse_prd(content: str, inferences: dict, subgraph: dict) -> str:
    """阶段 4:用 LLM 将推理结果融合回 PRD 原文,生成增强版。

    返回增强后的 Markdown 文本。
    """
    gen_cfg = _load_prd_generator_config()

    subgraph_text = _format_subgraph_for_llm(subgraph)
    inferences_text = json.dumps(inferences, ensure_ascii=False, indent=2)

    system_prompt = """你是 PRD 增广优化专家。你的任务是基于"本体知识推理结果",**对用户提供的 PRD 原文进行增广优化**--保留原文全部内容并用结构化方式补全隐含信息,输出一份**更完善的需求文档**。

## 输出格式(必须严格遵守)

使用如下 Markdown 结构,**逐段输出**,便于调用方 Agent 扫描和复用:

```
# 增强版 PRD:<一句话概括本次需求>

## 原始需求
> <将 PRD 原文用引用块完整呈现,不省略、不改写>

## 增强说明
<2~3 句话说明本次基于哪些本体知识(实体/关系)做了增广,便于调用方理解来源>

## 隐含依赖
- **<依赖对象 1>**:<一句话说明为什么这是隐含依赖,附本体证据>
- **<依赖对象 2>**:...

## 约束条件
- **<约束主题 1>**:<约束内容,附本体证据>
- **<约束主题 2>**:...

## 影响范围
- **<受影响模块 1>**:<影响内容,附本体证据>
- **<受影响模块 2>**:...

## 增强后的完整需求
<在此重写一份整合后的需求描述,把隐含依赖 / 约束 / 影响 **自然融合**到对应段落中(用加粗标记或括号注解),让整篇读起来像一份完善的需求文档>
```

## 增强原则

1. **保留原文**:原始需求章节必须**逐字保留**用户输入,不删不改。
2. **结构化优先**:隐含依赖/约束/影响用列表 + 加粗,方便调用方 Agent 抽取和呈现给用户。
3. **有据可依**:每条增强信息必须来自 `inferences` 或 `subgraph` 提供的证据,不要凭空捏造。
4. **自然融合**:在"增强后的完整需求"小节中,把上述信息自然地融入对应段落,使用 `**加粗**` 或 `(注解)` 等方式标注。
5. **不输出元信息**:不要在末尾追加"## 关系分析"等附录章节,也不要输出 JSON / 解释说明。
6. **空类目处理**:若某类目(如约束)无推理结果,写 "无新增约束"。
7. **直接输出 Markdown**,不要使用 ```markdown 代码块包裹。"""

    user_prompt = f"""## 用户输入的 PRD 原文
{content}

## 推理结果(来自本体子图的隐含依赖 / 约束 / 影响)
{inferences_text}

{subgraph_text}

请按照规定的 Markdown 结构输出增强版 PRD。"""

    resp = _call_llm_with_retry(
        system_prompt, user_prompt,
        temperature=gen_cfg["temperature"],
        max_tokens=gen_cfg["max_tokens"],
        max_retries=3,
    )
    # 清理可能残留的 JSON 块
    cleaned = re.sub(r"```json\n.*?\n```", "", resp, flags=re.DOTALL).strip()
    return cleaned if cleaned else resp


def parse_prd(content: str, db_path=None) -> dict:
    """解析 PRD 并返回增强版 PRD（旧版四阶段流水线，保留向后兼容）。

    流水线：
    1. LLM 实体抽取 - 从 PRD 文本提取功能级实体
    2. 语义匹配 + 图搜索 - 在本体中模糊匹配实体，BFS 遍历关系表获取子图
    3. LLM 推理 - 3 个并行 subagent 推理隐含依赖/约束/影响
    4. LLM 融合 - 将推理结果融合回 PRD 原文

    返回:
        enriched_prd: 增强后的 PRD Markdown
        summary: 解析摘要（含各阶段统计）
        pipeline_trace: 各阶段中间结果（用于调试）
    """
    # 阶段 1：LLM 实体抽取
    prd_entities = _extract_prd_entities(content)

    # 阶段 2：语义匹配 + 图搜索
    match_results = _semantic_match_entities(prd_entities, db_path)
    matched_ids = [m["matched_entity_id"] for m in match_results if m.get("match")]
    subgraph = _graph_search(matched_ids, db_path, max_depth=2)

    # 阶段 3：LLM 推理（3 个并行 subagent）
    inferences = _reason_inferences(content, subgraph)

    # 阶段 4：LLM 融合
    enriched_prd = _fuse_prd(content, inferences, subgraph)

    return {
        "enriched_prd": enriched_prd,
        "summary": {
            "entities_extracted": len(prd_entities),
            "entities_matched": len(matched_ids),
            "relations_found": len(subgraph.get("relations", [])),
            "inferences": {
                "dependencies": len(inferences.get("dependencies", [])),
                "constraints": len(inferences.get("constraints", [])),
                "impacts": len(inferences.get("impacts", [])),
            },
            "enhancement_mode": "fused",
            "pipeline_stages": ["extract", "match", "reason", "fuse"],
        },
        "pipeline_trace": {
            "stage1_entities": prd_entities,
            "stage2_matches": match_results,
            "stage2_subgraph": subgraph,
            "stage3_inferences": inferences,
        },
    }


# ---------------------------------------------------------------------------
# V2: 基于规则引擎的 PRD 增强流水线（2 次 LLM 调用）
# ---------------------------------------------------------------------------

def parse_prd_v2(content: str, db_path=None) -> dict:
    """解析 PRD 并返回增强版 PRD（V2 规则引擎流水线）。

    新流水线（2 次 LLM 调用）：
    1. [LLM] 实体抽取 + 语义匹配 - 从 PRD 文本提取实体并匹配到本体
    2. [规则引擎] 推理流水线 - 7 大规则 + 一致性检查
    3. [LLM] PRD 融合 - 将规则推理结果转为增强版 PRD

    返回:
        enriched_prd: 增强后的 PRD Markdown
        summary: 解析摘要（含各阶段统计）
        pipeline_trace: 各阶段中间结果（用于调试）
    """
    # 阶段 1: LLM 实体抽取 + 语义匹配（复用现有逻辑）
    prd_entities = _extract_prd_entities(content)
    match_results = _semantic_match_entities(prd_entities, db_path)
    matched_ids = [m["matched_entity_id"] for m in match_results if m.get("match")]

    if not matched_ids:
        # 没有匹配到任何实体，直接返回原文
        return {
            "enriched_prd": content,
            "summary": {
                "entities_extracted": len(prd_entities),
                "entities_matched": 0,
                "inferences": {},
                "enhancement_mode": "no_match",
                "pipeline_stages": ["llm_extract_match"],
            },
            "pipeline_trace": {
                "stage1_entities": prd_entities,
                "stage1_matches": match_results,
            },
        }

    # 阶段 2: 规则引擎推理（替换原来的阶段 2c + 3）
    from engine.core import ReasoningEngine
    from engine.rules.transitive import TransitiveClosureRule
    from engine.rules.symmetric import SymmetricRule
    from engine.rules.inverse import InverseRelationRule
    from engine.rules.constraint import ConstraintPropagationRule
    from engine.rules.impact import ImpactAnalysisRule
    from engine.rules.inheritance import InheritanceRule
    from engine.rules.conflict import ConflictDetectionRule
    from engine.checker import ConsistencyChecker

    engine = ReasoningEngine(db_path=db_path)
    engine.register_rule(TransitiveClosureRule())
    engine.register_rule(SymmetricRule())
    engine.register_rule(InverseRelationRule())
    engine.register_rule(ConstraintPropagationRule())
    engine.register_rule(ImpactAnalysisRule())
    engine.register_rule(InheritanceRule())
    engine.register_rule(ConflictDetectionRule())
    engine.register_checker(ConsistencyChecker())

    reasoning_output = engine.run(matched_ids)

    # 记录预测到反馈日志（自监督迭代框架）
    import uuid
    prediction_id = str(uuid.uuid4())[:8]
    try:
        from engine.feedback import log_prediction
        log_prediction(
            prediction_id=prediction_id,
            entity_ids=matched_ids,
            inferences=[r.to_dict() for r in reasoning_output.inferences],
            source="parse_prd_v2",
            db_path=db_path,
        )
    except Exception:
        pass  # 反馈日志失败不影响主流程

    # 阶段 3: LLM 融合（将规则推理结果转为增强版 PRD）
    enriched_prd = _fuse_prd_from_reasoning(content, reasoning_output)

    return {
        "enriched_prd": enriched_prd,
        "summary": {
            "entities_extracted": len(prd_entities),
            "entities_matched": len(matched_ids),
            "inferences": reasoning_output.stats,
            "enhancement_mode": "rule_engine",
            "pipeline_stages": ["llm_extract_match", "rule_engine", "llm_fuse"],
            "llm_calls": 2,
            "prediction_id": prediction_id,
        },
        "pipeline_trace": {
            "stage1_entities": prd_entities,
            "stage1_matches": match_results,
            "stage2_reasoning": reasoning_output.to_dict(),
        },
    }


def _fuse_prd_from_reasoning(content: str, reasoning_output) -> str:
    """将规则引擎的推理结果融合为增强版 PRD（单次 LLM 调用）。

    与 _fuse_prd 不同：推理结果来自规则引擎，每条都有明确的规则名称和证据。
    LLM 只负责将这些结构化推理结果转化为自然语言 PRD 增广内容。
    """
    gen_cfg = _load_prd_generator_config()
    reasoning_text = reasoning_output.to_llm_format()

    system_prompt = """你是 PRD 增广优化专家。基于规则推理引擎提供的推理结果，将用户 PRD 原文增广为完善的需求文档。

推理结果中的每条推理都有明确的规则名称、证据和置信度，请忠实呈现，不要添加未在推理结果中出现的信息。

## 输出格式（必须严格遵守）

使用如下 Markdown 结构，逐段输出：

```
# 增强版 PRD：<一句话概括本次需求>

## 原始需求
> <将 PRD 原文用引用块完整呈现，不省略、不改写>

## 增强说明
<2~3 句话说明本次基于哪些本体知识做了增广，列出来自规则引擎的推理类型>

## 隐含依赖
- **<依赖对象 1>**：<一句话说明推理依据，附规则名称和证据>
- **<依赖对象 2>**：...
（如无推理结果，写 "未发现隐含依赖"）

## 约束条件
- **<约束主题 1>**：<约束内容，附规则名称和证据>
（如无推理结果，写 "无新增约束"）

## 影响范围
- **<受影响模块 1>**：<影响内容，附规则名称和证据>
（如无推理结果，写 "无影响范围推理结果"）

## ⚠️ 冲突检测
- **<冲突描述>**：<冲突详情，附规则名称>
（如无冲突，写 "未检测到冲突"）

## 增强后的完整需求
<在此重写一份整合后的需求描述，把隐含依赖 / 约束 / 影响 自然融合到对应段落中（用加粗标记或括号注解），让整篇读起来像一份完善的需求文档>
```

## 增强原则

1. **保留原文**：原始需求章节必须逐字保留用户输入，不删不改。
2. **忠实推理**：每条增强信息必须来自推理结果，不要凭空捏造。引用规则名称和证据。
3. **结构化优先**：用列表 + 加粗，方便调用方 Agent 抽取。
4. **空类目处理**：若某类目无推理结果，写对应的无结果提示。
5. **直接输出 Markdown**，不要使用代码块包裹。
"""

    user_prompt = f"""## 用户输入的 PRD 原文
{content}

## 规则引擎推理结果
{reasoning_text}

请按照规定的 Markdown 结构输出增强版 PRD。"""

    resp = _call_llm_with_retry(
        system_prompt, user_prompt,
        temperature=gen_cfg["temperature"],
        max_tokens=gen_cfg["max_tokens"],
        max_retries=3,
    )
    # 清理可能残留的 JSON 块
    cleaned = re.sub(r"```json\n.*?\n```", "", resp, flags=re.DOTALL).strip()
    return cleaned if cleaned else resp
