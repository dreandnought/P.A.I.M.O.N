# P.A.I.M.O.N

**P**RD **A**lgorithm of **I**ntelligent **M**atrix and **O**ntology **N**etwork

---

## ⚜️ 原初永恒统辖矩阵

> *在提瓦特大陆，存在着维持世界运转的永恒规则。*
> *天理制定法则，统辖矩阵执行法则——它检查每个实体的位置、每条关系的走向、每处矛盾的裂隙，让万千元素与因果在秩序中流转。*
> *而派蒙，是旅行者踏遍七国、理解这个世界的向导。*

**P.A.I.M.O.N** 的名字源自《原神》中"原初永恒统辖矩阵"（Algorithm of Semi-Intransient Matrix of Overseer Network）的意象——一个维护规则一致性、推理隐含依赖、监控异常冲突的智能系统。而我们项目中的推理引擎与一致性检查器，正是为 Coding Agent 构建的一座这样的"统辖矩阵"：

| 原神概念 | P.A.I.M.O.N 对应 |
|---------|----------------|
| **天理**（世界规则制定者） | **Schema 层** — 实体类型定义、关系类型语义、类型层次继承 |
| **原初永恒统辖矩阵**（规则执行/监控） | **推理引擎 + 一致性检查器** — 7 大规则自动推理、矛盾检测、循环依赖检测 |
| **派蒙**（向导、接口人） | **MCP 工具 + Web 看板** — 用户/Agent 与本体知识库交互的接口 |
| **提瓦特大陆** | **本体数据库（Ontology DB）** — 实体和关系构成的知识世界 |
| **地脉**（连接万物的网络） | **关系图** — depends_on / causes / impacts 的关系网络 |
| **元素反应** | **冲突检测** — 检测互斥、矛盾、循环依赖 |

> **P.A.I.M.O.N** — 让 Coding Agent 像旅行者借助派蒙一样，理解复杂项目的知识世界。

---

为 Coding Agent（Claude Code、Cline 等）提供 PRD 增强理解的 MCP 插件。

通过将 PRD 中的实体和隐性关系建模为显式 Ontology 图，让 Agent 不仅能"读"PRD，更能"理解"其中的因果链、约束关系、依赖网络。

## ✨ 核心能力

### 🧩 知识建模：把"文档"变成"图"

传统 PRD 是一段线性文字，实体和关系藏在段落中，Agent 只能逐字读取。P.A.I.M.O.N 将 PRD 中的功能、模块、接口、约束、测试用例等实体抽取出来，连同它们之间的依赖、因果、影响、冲突关系，构建为一张显式的 **本体知识图（Ontology Graph）**。

```
传统方式：Agent 读 PRD → 逐句理解 → 靠模型上下文猜测关系
P.A.I.M.O.N：PRD → 本体抽取 → 规则推理 → 增强版 PRD（含完整关系图谱）
```

### 🔮 推理引擎：7 大规则自动发现隐藏知识

基于确定性规则推理，而非 LLM 语义猜测。每条推理都有规则名称和明确证据，结果完全可复现。

| 规则 | 能力 | 例子 |
|------|------|------|
| **传递闭包** | 递归计算多跳间接依赖 | A→B, B→C ⇒ A→C |
| **对称推理** | 对称关系自动推导反向 | A 关联 B ⇒ B 关联 A |
| **逆关系推理** | 正向关系的逆向语义 | A 包含 B ⇒ B 属于 A |
| **约束传播** | 约束沿包含关系传播 | 模块有约束 ⇒ 子功能也有 |
| **影响分析** | BFS 遍历关系网络 | 修改 A 会影响哪些实体？ |
| **类型继承** | 兄弟实体共性关系推导 | 同类型的实体共享关系 |
| **冲突检测** | 4 种矛盾模式检测 | 依赖且冲突 / 循环包含 |

**效果**：一次 PRD 解析，LLM 调用从 6 次降至 2 次，规则引擎可产出 **67 条以上**推理结果，PRD 从 276 字符增强至 5,438 字符。

### 🛡️ 一致性检查器：维护本体健康

像统辖矩阵监控提瓦特一样，检查器持续监控本体的一致性：

- **类型兼容性检查** — 关系两端的实体类型是否合理（如 test_case 不应 contains module）
- **孤立实体检查** — 标记没有任何关系的"孤岛"实体
- **循环依赖检测** — 递归 CTE 检测 A→B→...→A 的死循环
- **置信度异常检测** — 过低或反常的高置信度标记
- **矛盾关系检测** — 同时存在 depends_on 和 conflicts_with 的关系

### 🔄 自监督反馈工作流

记录每次推理预测，Agent 可在实际开发后提交验证结果，形成持续改进的闭环：

```
推理引擎产生预测 → Agent 开发验证 → 提交反馈 → 统计准确率 → 校准参数
```

当前（Phase 4）支持预测记录、反馈提交、准确率统计；Phase 5+ 将实现自动参数调优。

### ⚡ 推理缓存

推理结果自动缓存（内存级 LRU + TTL），实体/关系变更时自动失效。Web 看板可查看缓存命中率统计。

---

## 快速开始

### 环境要求

- **Python** >= 3.10
- 网络连接（需要访问 LLM API）

### 1. 安装依赖

```bash
cd CodingOntology
pip install -r requirements.txt
```

### 2. 启动前端看板

```bash
cd CodingOntology/web
python3 app.py
```

默认在 `http://localhost:5258` 访问。
前端看板可以观察目前已有的本体中每个实体之间的关系，以及每个实体的详细信息。
同时还可以配置 LLM API，LLM 能力将用于解析 PRD 和查询本体。

### 3. 配置 LLM API

直接在前端面板上配置；
或手动编辑 `llm_config.yaml`，填入有效的 API Key：

```yaml
default_provider: siliconflow

providers:
  siliconflow:
    api_key: "sk-your-api-key-here"       # 替换为你的 Key
    base_url: "https://api.siliconflow.cn/v1"
    models:
      chat: "deepseek-ai/DeepSeek-V4-Flash"   # 或 deepseek-ai/DeepSeek-V4-Pro

  # 也可配置其他兼容 OpenAI 协议的提供商
  openai:
    api_key: ""
    base_url: "https://api.openai.com/v1"
    models:
      chat: "gpt-4o"
```

> **注意**：`llm_config.yaml` 在生产环境中会明文存储 API Key。由于此 MCP Server 部署在局域网供团队共用，这是可接受的方案。如果需要更高安全性，可通过环境变量 `LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL` 覆盖配置。

### 4. 启动 MCP Server

```bash
python3 server.py
```

默认以 stdio 模式运行，等待 Coding Agent 通过标准输入/输出通信。

## 接入 Coding Agent

### 接入 Claude Code

```bash
# 添加 MCP Server（从项目目录执行）
claude mcp add paimon \
  -- python /absolute/path/to/CodingOntology/server.py
```

### 接入 Cline / Cursor / Trae

在 MCP 配置文件中添加：

```json
{
  "mcpServers": {
    "paimon": {
      "command": "python3",
      "args": ["/absolute/path/to/CodingOntology/server.py"]
    }
  }
}
```

配置后，Coding Agent 将能使用以下工具（共 8 个）：

| 工具 | 说明 |
|------|------|
| `parse_prd` | 解析 PRD 文档，通过规则推理引擎返回增强版 PRD（只读，不修改数据库） |
| `query_ontology` | 查询 Ontology 中的实体和关系（按名称/ID/关键词），可选附加推理结果 |
| `ingest_document` | 从文本/Markdown 文档抽取实体和关系，写入 Ontology 数据库 |
| `modify_ontology` | 根据自然语言描述修改已有 Ontology（增/删/改实体和关系） |
| `delete_ontology_entity` | 根据实体 ID 或名称直接删除实体及其所有关联关系 |
| `manage_schema` | 管理本体 Schema 层（查看/修改实体类型、关系类型语义） |
| `reason_ontology` | **直接暴露推理引擎**，对指定实体运行 7 大规则推理，返回隐含依赖/约束/影响/冲突 |
| `manage_feedback` | **自监督反馈工作流**，提交/查询推理结果的验证反馈，追踪自监督迭代进度 |

### 8 个工具详解

#### ① `parse_prd` — PRD 增强解析

核心入口。输入一段 PRD 文档，P.A.I.M.O.N 的 V2 流水线会：

1. **LLM 抽取** — 从 PRD 中抽取第一层实体（功能级）
2. **本体匹配** — 在已有本体中匹配实体
3. **规则推理** — 推理引擎自动发现隐含依赖、约束、影响、冲突
4. **LLM 融合** — 将推理结果整理为 Markdown 附录追加到 PRD 末尾

返回增强版 PRD 可直接用于 Agent 的任务规划。

#### ② `query_ontology` — 本体查询

查询已有本体中的实体和关系。支持三种查询方式：
- **按实体 ID 查询** — 精确查找
- **按实体名称查询** — 模糊匹配
- **关键词搜索** — 匹配名称和描述

可选附加推理结果（`include_inferences=true`），返回该实体的完整依赖、约束和影响网络。

#### ③ `ingest_document` — 知识摄入

从文本或 Markdown 文档中抽取实体和关系，写入本体数据库。支持 `dry_run` 模式：
- `dry_run=true` — 预览变更计划，不实际写入
- `dry_run=false` — 确认后执行写入

#### ④ `modify_ontology` — 本体修改

用自然语言描述对已有本体的修改。支持增、删、改实体和关系。同样支持 `dry_run` 模式预览变更。

#### ⑤ `delete_ontology_entity` — 实体删除

直接根据实体 ID 或名称删除实体及其所有关联关系。操作不可逆，需谨慎使用。

#### ⑥ `manage_schema` — Schema 管理

管理本体的 Schema 层——定义实体类型、关系类型、类型层次、关系语义（如 `inverse_of`、`transitive`、`domain_type` / `range_type`）。

#### ⑦ `reason_ontology` — 直接推理 🔥

**直接暴露推理引擎**，对指定实体运行 7 大规则推理。支持：
- 按实体 ID / 名称 / 关键词三种输入方式
- 按规则筛选（`rules_only` 参数）
- 按推理类型筛选（`inference_type` 参数）
- 返回 `llm_summary` 可直接用于 Agent 上下文

这是"统辖矩阵"的核心能力——像地脉网络一样，自动发现实体间隐藏的关系链。

#### ⑧ `manage_feedback` — 自监督反馈

提交和查询推理结果的验证反馈。每次 `parse_prd` 自动记录 `prediction_id`，Agent 可在开发完成后提交验证，系统统计准确率，为未来自监督学习积累数据。

## Trae Skills（推荐用法）

项目已在 `.trae/skills/` 下封装了 5 个 Skill，Coding Agent 可直接根据意图调用对应 Skill，自动完成与 MCP 的交互。

| Skill | 触发场景 | 对应 MCP 工具 |
|-------|---------|--------------|
| `query-ontology` | 用户想查询某个实体、查看关系或搜索本体知识库 | `query_ontology` |
| `parse-prd` | 用户输入 PRD 或需求文档，希望利用本体知识完善它 | `parse_prd` |
| `ingest-document` | 用户输入一段文字/Markdown，要求提取实体和关系并写入本体 | `ingest_document` |
| `delete-ontology-entity` | 用户要求删除本体中的某个实体及其关系 | `delete_ontology_entity` |
| `prd-ontology-seeder` | 用户要求从代码库、API 文档等初始化预设 Ontology | 组合使用 `ingest_document` + `manage_schema` |

Skill 文件位于：

- `.trae/skills/query-ontology/SKILL.md`
- `.trae/skills/parse-prd/SKILL.md`
- `.trae/skills/ingest-document/SKILL.md`
- `.trae/skills/delete-ontology-entity/SKILL.md`
- `.trae/skills/prd-ontology-seeder/SKILL.md`

每个 SKILL.md 中详细说明了参数、调用示例和返回值。Agent 识别到对应意图时，优先使用 Skill 中描述的工具和参数模板，无需手动拼装复杂 JSON。

### 将 Skill 配置到 Trae

Trae 会在当前工作区的 `.trae/skills/` 目录自动识别自定义 Skill。配置步骤如下：

1. **确认目录结构**

   将本项目的 Skill 复制或软链接到工作区根目录的 `.trae/skills/` 下，确保每个 Skill 都有独立的子目录和 `SKILL.md`：

   ```
   CodingOntology/
   ├── .trae/
   │   └── skills/
   │       ├── query-ontology/
   │       │   └── SKILL.md
   │       ├── parse-prd/
   │       │   └── SKILL.md
   │       ├── ingest-document/
   │       │   └── SKILL.md
   │       ├── delete-ontology-entity/
   │       │   └── SKILL.md
   │       ├── prd-ontology-seeder/
   │       │   └── SKILL.md
   │       └── prd-writer/
   │           ├── SKILL.md
   │           └── PROMPT.md
   │   ...
   ```

   快捷方式（Linux/macOS）：

   ```bash
   cd /path/to/CodingOntology
   # 如果工作区根目录就是本项目，已自带 .trae/skills，无需复制
   # 如果是其他项目想用这些 Skill，可软链过去
   ln -s /path/to/CodingOntology/.trae/skills /path/to/other-project/.trae/skills
   ```

2. **重启或刷新 Trae**

   修改 `.trae/skills/` 后，重启 Trae 或重新打开当前工作区，Agent 即可加载新的 Skill。

3. **验证 Skill 已生效**

   在 Trae 的输入框发送与某个 Skill 触发条件相关的指令（如"查询 Gateway 模块的依赖关系"），Agent 应自动按对应 SKILL.md 的说明执行，无需手动指定 MCP 工具。

## 使用示例

### 1. 解析 PRD（只读，不修改数据库）

向 Agent 发送类似指令：

```
请用 parse_prd 技能分析以下 PRD：

# 用户登录系统
## 功能需求
1. 用户可以通过手机号或邮箱登录
2. 登录需要短信验证码验证
3. 登录失败超过5次将锁定账号30分钟
```

Agent 会调用 `parse_prd` 工具，返回增强版 PRD（含关系附录）。

### 2. 从文档中抽取新知识并写入本体

```
请使用 ingest_document 技能将以下文档中的实体和关系摄入本体数据库：

# 支付模块
## 功能
1. 支持微信支付和支付宝支付
2. 支付前必须完成实名认证
3. 支付完成后发送短信通知

先 dry_run=true 预览变更计划，确认后再 dry_run=false 执行写入。
```

Agent 会先调用 `ingest_document(dry_run=true)` 返回变更计划，经你确认后再调用 `ingest_document(dry_run=false)` 写入数据库。

### 3. 用自然语言修改已有本体

```
请使用 modify_ontology 技能：把"用户登录"实体的描述更新为支持手机号、邮箱和微信扫码登录，并添加"用户登录"依赖"微信 OAuth 接口"的关系。

target_entity_id 为 func:user_login，先 dry_run=true 预览，确认后再执行。
```

Agent 会先调用 `modify_ontology(dry_run=true)` 返回变更计划，确认后再执行。

### 4. 查询 Ontology

```
使用 query_ontology 技能查询 Gateway 模块的依赖关系
```

Agent 会调用 `query_ontology` 返回实体信息和关联关系。

### 5. 运行推理引擎

```
使用 reason_ontology 技能，分析"用户登录"实体的所有隐含依赖、约束和影响范围
```

Agent 会调用 `reason_ontology`，返回推理引擎对"用户登录"实体运行 7 大规则后的完整推理结果，包括传递依赖链、约束传播、影响范围等。

### 6. 提交反馈

```
使用 manage_feedback 技能，提交对 prediction_id: pred_abc123 的验证反馈：
- 正确推理数：8
- 错误推理数：1
- 备注：依赖关系正确，冲突检测有误报
```

Agent 会调用 `manage_feedback` 记录反馈信息，用于后续准确率统计和参数校准。

## 本体数据库

当前数据库由测试数据生成，原始数据位于 `test_data/` 目录下，主要由 claude-code-best（CCB）项目的代码分析文档、Anthropic 公司相关信息组成，仅供测试使用。若您需要建立自己的本体数据库，可清理掉 `ontology.db`，然后使用 Skill 重新建立数据库。

| 领域 | 实体数 | 来源 |
|:----|:------:|:----|
| CCB (Claude Code best) | 73 | test_data/claude-code-*.md |
| Anthropic 公司 | 40 | test_data/anthropic-company/*.md |

### 快速初始化自定义本体

```python
from models.schema import get_connection

def seed_new_domain():
    conn = get_connection()
    
    # 实体列表：[id, type_id, name, description]
    entities = [
        ("prefix:entity_id", "function", "实体名称", "实体描述"),
    ]
    for eid, etype, ename, edesc in entities:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type_id, name, description, confidence, source) VALUES (?, ?, ?, ?, 0.90, 'manual')",
            (eid, etype, ename, edesc),
        )
    
    # 关系列表：[source_id, type_id, target_id, confidence, description]
    relations = [
        ("source_id", "depends_on", "target_id", 0.95, "关系描述"),
    ]
    for src, rtype, tgt, conf, desc in relations:
        conn.execute(
            "INSERT OR IGNORE INTO relations (id, type_id, source_id, target_id, weight, confidence, source, metadata) VALUES (?, ?, ?, ?, 1.0, ?, 'manual', ?)",
            (f"{src}__{rtype}__{tgt}", rtype, src, tgt, conf, f'{{"description": "{desc}"}}'),
        )
    
    conn.commit()
    conn.close()
```

---

## 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 运行时 | Python + FastMCP | 轻量、成熟、MCP 标准 |
| 存储 | SQLite | 零依赖、嵌入式、无需部署 |
| 推理 | 确定性规则引擎 | 7 大规则，可复现，无需 LLM |
| LLM | DeepSeek / GPT / 任意 OpenAI 兼容 | 灵活切换，支持国内/国际 |
| 缓存 | 内存 LRU + TTL | 进程级别，实体变更自动失效 |
| 前端 | Flask + HTML/JS | 轻量看板，可视化本体和推理结果 |

## 源码统计

- Python 源码：40 个文件，8,546 行
- 测试代码：2,308 行
- 16 个单元测试全部通过

---

> *"派蒙是派蒙，但统辖矩阵是统辖矩阵……"*  
> *——不，它们是同一件事的一体两面。规则创造世界，派蒙带你理解它。*
