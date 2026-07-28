---
name: prd-ontology-seeder
description: >-
  从项目文档中抽取实体和关系，构建/更新 PRD Ontology MCP 的本体数据库
---

# PRD Ontology Seeder

## 用途

按照 `ONTOLOGY_BUILD_GUIDE.md` 定义的协议，从文档中抽取实体和关系，写入 `prd-ontology-mcp/ontology.db`。供需要构建或扩展本体数据库的 Agent 使用。

## 数据库位置

```
{baseDir}/../ontology.db
```

项目目录：`{baseDir}/..`（即 `prd-ontology-mcp/`）

## 数据库 Schema

`models/schema.py` 中的 `init_db()` 函数初始化以下核心表：

| 表名 | 说明 |
|------|------|
| `entity_types` | 实体类型定义（预置 8 种） |
| `relation_types` | 关系类型定义（预置 10 种） |
| `entities` | 实体实例（核心表） |
| `relations` | 关系实例（核心表） |
| `documents` | 来源文档记录 |
| `entity_history` | 实体变更历史 |
| `relation_history` | 关系变更历史 |
| `feedback_log` | 自监督反馈日志 |

## 实体 ID 命名规范

使用前缀区分实体来源领域：

| 前缀 | 领域 | 示例 |
|------|------|------|
| `mod:` | OpenClaw 平台模块 | `mod:gateway` |
| `func:` | OpenClaw 平台功能 | `func:message_send` |
| `iface:` | 接口 | `iface:feishu_api` |
| `con:` | 约束 | `con:memory_limit_3_8gb` |
| `ccb:mod:` | CCB 项目模块 | `ccb:mod:query_engine` |
| `ccb:func:` | CCB 项目功能 | `ccb:func:repl_interaction` |
| `ccb:iface:` | CCB 项目接口 | `ccb:iface:anthropic_api` |
| `acomp:` | Anthropic 公司实体 | `acomp:anthropic_inc` |
| `acomp:prod:` | Anthropic 产品 | `acomp:prod:claude_4_opus` |
| `acomp:tech:` | 技术模块 | `acomp:tech:constitutional_ai` |
| `acomp:data:` | 数据实体 | `acomp:data:gpu_cluster` |
| `acomp:req:` | 需求 | `acomp:req:safety_first` |
| `acomp:con:` | 约束 | `acomp:con:claude_5_shutdown` |

**新增领域时**：请使用 `{领域缩写}:{类型缩写}:{名称}` 格式，保持可读性。

## 实体类型

预置 8 种实体类型，定义在 `entity_types` 表中：

| id | name | 说明 |
|----|------|------|
| `requirement` | 需求 | 业务需求或用户需求 |
| `function` | 功能 | 系统功能点 |
| `module` | 模块 | 代码模块或子系统 |
| `interface` | 接口 | API 接口或服务接口 |
| `data_entity` | 数据实体 | 数据库表或数据模型 |
| `test_case` | 测试用例 | 测试用例或测试场景 |
| `constraint` | 约束 | 业务约束或技术约束 |
| `actor` | 角色 | 用户角色或系统角色 |

## 关系类型

预置 10 种关系类型：

| id | name | 对称 | 可传递 | 说明 |
|----|------|:----:|:------:|------|
| `depends_on` | 依赖 | × | ✅ | A 依赖 B |
| `causes` | 因果 | × | ✅ | A 导致 B |
| `constrains` | 约束 | × | × | A 约束 B |
| `impacts` | 影响 | × | ✅ | A 影响 B |
| `conflicts_with` | 冲突 | ✅ | × | A 与 B 冲突 |
| `derived_from` | 派生 | × | ✅ | A 派生自 B |
| `implements` | 实现 | × | × | A 实现 B |
| `contains` | 包含 | × | ✅ | A 包含 B |
| `refines` | 细化 | × | ✅ | A 细化 B |
| `relates_to` | 关联 | ✅ | × | A 与 B 关联 |

## 抽取流程

### Step 1：阅读文档

读取需要抽取的文档内容，理解其结构。文档通常是 Markdown 格式，可能包含章节、表格、列表。

### Step 2：识别实体

从文档中识别以下类型的实体：

- **公司/组织** → `actor` 类型
- **产品/功能** → `function` 类型
- **技术/模块** → `module` 类型
- **接口/API** → `interface` 类型
- **数据/资源** → `data_entity` 类型
- **需求/目标** → `requirement` 类型
- **约束/限制** → `constraint` 类型
- **角色/人** → `actor` 类型

每个实体需要：`id`、`type_id`、`name`、`description`（60-100 字，描述核心特征）。

### Step 3：识别关系

实体之间的关系，常见的模式：

| 模式 | 关系类型 | 示例 |
|------|---------|------|
| X 是 Y 的一部分 | `contains` | 产品系列包含具体产品 |
| X 使用/依赖 Y | `depends_on` | 产品依赖技术 |
| X 影响 Y | `impacts` | 事件影响进程 |
| X 约束 Y | `constrains` | 监管约束产品 |
| X 实现 Y | `implements` | 技术实现需求 |
| X 与 Y 有关联 | `relates_to` | 投资关系、竞争关系 |

### Step 4：写入数据库

使用 `INSERT OR IGNORE` 写入，避免重复。关系使用唯一 ID `{source_id}__{type_id}__{target_id}`。

示例代码结构：

```python
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from models.schema import get_connection

def seed_new_domain():
    conn = get_connection()
    
    # 实体列表
    entities = [
        ("prefix:entity_id", "type_id", "实体名称", "实体描述（60-100字）"),
    ]
    
    # 插入实体
    for eid, etype, ename, edesc in entities:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type_id, name, description, confidence, source) VALUES (?, ?, ?, ?, 0.90, 'manual')",
            (eid, etype, ename, edesc),
        )
    
    # 关系列表
    relations = [
        ("source_id", "type_id", "target_id", 0.95, "关系描述"),
    ]
    
    # 插入关系
    for src, rtype, tgt, conf, desc in relations:
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, weight, confidence, source, metadata) VALUES (?, ?, ?, ?, 1.0, ?, 'manual', ?)",
            (f"{src}__{rtype}__{tgt}", rtype, src, tgt, conf, f'{{"description": "{desc}"}}'),
        )
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    seed_new_domain()
```

## 已有领域

当前数据库中已有的实体前缀和对应的源文档：

| 前缀 | 领域 | 源文档 |
|------|------|--------|
| `mod:`, `func:`, `iface:`, `con:`, `data:` | OpenClaw 平台 | AGENTS.md, SOUL.md, TOOLS.md, knowledge_base/ |
| `ccb:` | CCB (Claude Code) | test_data/claude-code-*.md |
| `acomp:` | Anthropic 公司 | test_data/anthropic-company/*.md |

## 验证

写入后可通过以下查询验证数据正确性：

```python
from models.schema import get_connection
conn = get_connection()

# 按类型统计
rows = conn.execute('''
    SELECT et.name AS type_name, COUNT(*) AS cnt 
    FROM entities e JOIN entity_types et ON e.type_id = et.id 
    GROUP BY e.type_id ORDER BY cnt DESC
''').fetchall()

# 按前缀统计
rows = conn.execute('''
    SELECT 
        CASE 
            WHEN id LIKE 'acomp:%' THEN 'Anthropic 公司'
            WHEN id LIKE 'ccb:%' THEN 'CCB 项目'
            ELSE 'OpenClaw 平台'
        END AS domain,
        COUNT(*) AS cnt
    FROM entities GROUP BY domain
''').fetchall()

conn.close()
```
