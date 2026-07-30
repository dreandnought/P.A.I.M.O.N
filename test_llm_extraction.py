"""
test_llm_extraction.py - 轻量版

不传 ontology 上下文（避免 10K context 让 LLM 慢），让 LLM 专心从文档抽实体和关系。
然后在本地做实体匹配（用 LIKE 模糊匹配已有 ontology.db 的 name 字段）。
"""

import os
import sys
import json
import sqlite3
import time
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from parser.llm_parser import _call_llm, _first_json_block, _extract_json_blocks

# 数据库
db = sqlite3.connect("ontology.db")
db.row_factory = sqlite3.Row
existing = {r["id"]: dict(r) for r in db.execute("select id, name, type_id, description from entities").fetchall()}
existing_by_name = {r["name"].lower(): r["id"] for r in db.execute("select id, name from entities").fetchall()}
print(f"[init] ontology.db 已有 {len(existing)} 个实体", flush=True)


SYSTEM_PROMPT = """你是一个本体知识抽取专家。从用户提供的技术文档中抽取**功能级**实体和它们之间的关系。

只返回一个 JSON 代码块,严格按以下格式:

```json
{
  "entities": [
    {
      "name": "用户登录",
      "type": "function",
      "description": "用户通过手机号或邮箱登录系统",
      "suggested_id": "func:user_login"
    }
  ],
  "relations": [
    {
      "source_name": "用户登录",
      "relation_type": "depends_on",
      "target_name": "短信验证码",
      "description": "登录需要短信验证码",
      "confidence": 0.9
    }
  ]
}
```

规则:
1. 实体类型仅限 8 个:requirement、function、module、interface、data_entity、test_case、constraint、actor。
2. suggested_id 用"类型前缀:英文小写slug",例如 function→func:xxx、module→mod:xxx、interface→iface:xxx、data_entity→data:xxx、actor→actor:xxx、requirement→req:xxx、constraint→con:xxx、test_case→test:xxx。
3. relation_type 仅限 10 个:depends_on、causes、constrains、impacts、conflicts_with、derived_from、implements、contains、refines、relates_to。
4. 只抽取文档中**明确提到**的实体和关系,不要过度推断。
5. 重点关注:技术架构、因果链(causes)、版本演进(derived_from/refines)、模块依赖(depends_on)、功能实现(implements)、业务影响(impacts)、冲突矛盾(conflicts_with)。
6. 实体和关系保持在 5-30 条之间,过多会失精。"""


def fuzzy_match(name: str) -> str | None:
    """本地模糊匹配到已有 ontology 实体"""
    nl = name.lower().strip()
    if nl in existing_by_name:
        return existing_by_name[nl]
    # 包含匹配
    for ename, eid in existing_by_name.items():
        if nl in ename or ename in nl:
            return eid
    return None


def extract_no_context(content: str) -> dict:
    """不带 ontology 上下文的抽取"""
    user_prompt = f"""## 待分析的文档
{content[:6000]}

请抽取实体和关系,返回严格 JSON。"""
    resp = _call_llm(SYSTEM_PROMPT, user_prompt, temperature=0.1, max_tokens=2048)
    parsed = _first_json_block(resp)
    if not isinstance(parsed, dict):
        return {"entities": [], "relations": []}
    return parsed


# 找文档
TEST_DATA = Path(__file__).parent / "test_data"
DOCS = [
    TEST_DATA / "anthropic-company" / "01-company-overview.md",
    TEST_DATA / "anthropic-company" / "03-claude-product-overview.md",
    TEST_DATA / "anthropic-company" / "04-core-technology.md",
    TEST_DATA / "anthropic-company" / "10-regulatory-environment.md",
]

# 总览
total_stats = {"docs": 0, "entities": 0, "relations": 0, "matched": 0,
               "by_rel_type": Counter(), "by_entity_type": Counter(),
               "new_rel_types": Counter()}  # 在 4 个挂零关系上新增的

for doc_path in DOCS:
    if not doc_path.exists():
        continue
    print(f"\n{'='*70}", flush=True)
    print(f"文档: {doc_path.name}  ({doc_path.stat().st_size} 字节)", flush=True)
    print('='*70, flush=True)

    content = doc_path.read_text(encoding="utf-8")
    t0 = time.time()
    try:
        result = extract_no_context(content)
    except Exception as e:
        print(f"[FAIL] {e}", flush=True)
        continue
    dt = time.time() - t0

    entities = result.get("entities", [])
    relations = result.get("relations", [])

    # 清洗 + 匹配
    valid_entity_types = {"requirement", "function", "module", "interface", "data_entity", "test_case", "constraint", "actor"}
    valid_rel_types = {"depends_on", "causes", "constrains", "impacts", "conflicts_with", "derived_from", "implements", "contains", "refines", "relates_to"}

    cleaned_ents = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        t = e.get("type", "")
        if t not in valid_entity_types:
            continue
        matched = fuzzy_match(e.get("name", ""))
        cleaned_ents.append({**e, "type": t, "matched": matched})

    cleaned_rels = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        rt = r.get("relation_type", "")
        if rt not in valid_rel_types:
            continue
        cleaned_rels.append(r)

    n_matched = sum(1 for e in cleaned_ents if e["matched"])
    print(f"\n耗时: {dt:.1f}s  实体: {len(cleaned_ents)}  关系: {len(cleaned_rels)}  匹配: {n_matched}", flush=True)

    ent_dist = Counter(e["type"] for e in cleaned_ents)
    rel_dist = Counter(r["relation_type"] for r in cleaned_rels)
    print(f"实体类型: {dict(ent_dist)}", flush=True)
    print(f"关系类型: {dict(rel_dist)}", flush=True)

    print(f"\n[实体]", flush=True)
    for e in cleaned_ents[:15]:
        match_str = f"→{e['matched']}" if e["matched"] else "新"
        print(f"  {e['type']:<12} {e['name']:<28} {match_str}", flush=True)
    if len(cleaned_ents) > 15:
        print(f"  ... 还有 {len(cleaned_ents)-15} 个", flush=True)

    print(f"\n[关系]", flush=True)
    for r in cleaned_rels[:15]:
        marker = "🟢" if r["relation_type"] in ("causes", "refines", "derived_from", "conflicts_with") else "  "
        print(f"  {marker}{r.get('source_name',''):<22} --[{r['relation_type']:<14}]--> {r.get('target_name',''):<22} conf={r.get('confidence',0):.2f}", flush=True)
        if r.get("description"):
            print(f"      {r['description'][:90]}", flush=True)
    if len(cleaned_rels) > 15:
        print(f"  ... 还有 {len(cleaned_rels)-15} 条", flush=True)

    # 累计
    total_stats["docs"] += 1
    total_stats["entities"] += len(cleaned_ents)
    total_stats["relations"] += len(cleaned_rels)
    total_stats["matched"] += n_matched
    for t, n in ent_dist.items():
        total_stats["by_entity_type"][t] += n
    for t, n in rel_dist.items():
        total_stats["by_rel_type"][t] += n
        if t in ("causes", "refines", "derived_from", "conflicts_with"):
            total_stats["new_rel_types"][t] += n

# 汇总
print(f"\n{'='*70}", flush=True)
print(f"📊 总览 ({total_stats['docs']} 篇文档)", flush=True)
print('='*70, flush=True)
print(f"  总实体: {total_stats['entities']}, 匹配已有: {total_stats['matched']} ({100.0*total_stats['matched']/max(1,total_stats['entities']):.1f}%)", flush=True)
print(f"  总关系: {total_stats['relations']}", flush=True)
print(f"  实体类型分布: {dict(total_stats['by_entity_type'])}", flush=True)
print(f"  关系类型分布:", flush=True)
for t, n in total_stats['by_rel_type'].most_common():
    marker = "🟢" if t in ("causes", "refines", "derived_from", "conflicts_with") else "  "
    print(f"    {marker}{t:<14} {n}", flush=True)
print(f"\n  关键观察:", flush=True)
print(f"    🔴 挂零类型新增情况: {dict(total_stats['new_rel_types'])}", flush=True)
print(f"    🟡 causes: 文档里有因果链吗?", flush=True)
print(f"    🟡 derived_from: 文档里有版本/架构派生吗?", flush=True)
print(f"    🟡 refines: 文档里有功能细化吗?", flush=True)
print(f"    🟡 conflicts_with: 文档里有矛盾吗?", flush=True)
