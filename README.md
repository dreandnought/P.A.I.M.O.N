# Coding Ontology MCP

为 Coding Agent（Claude Code、Cline 等）提供 PRD 增强理解的 MCP 插件。

通过将 PRD 中的实体和隐性关系建模为显式 Ontology 图，让 Agent 不仅能"读"PRD，更能"理解"其中的因果链、约束关系、依赖网络。

## 快速开始

### 环境要求

- **Python** >= 3.10
- 网络连接（需要访问 LLM API）

### 1. 安装依赖
这个requirements.txt文件包含了项目的前端与mcp服务器所有依赖。

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
同时还可以配置LLM API，LLM能力将用于解析PRD和查询本体。

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
claude mcp add prd-ontology \
  -- python /absolute/path/to/prd-ontology-mcp/server.py
```

### 接入 Cline / Cursor /Trae

在 MCP 配置文件中添加：

```json
{
  "mcpServers": {
    "coding-ontology": {
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

   在 Trae 的输入框发送与某个 Skill 触发条件相关的指令（如“查询 Gateway 模块的依赖关系”），Agent 应自动按对应 SKILL.md 的说明执行，无需手动指定 MCP 工具。

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

## 本体数据库

当前数据库由测试数据生成，原始数据位于test_data/目录下,主要由claude-code-best（ccb）项目的代码分析文档、Anthropic公司相关信息（有很多是AI编的）组成，仅供测试使用，若您需要建立自己的本体数据库，可清理掉ontology.db，然后使用skill重新建立数据库。

| 领域 | 实体数 | 来源 |
|:----|:------:|:----|
| CCB (Claude Code best) | 73 | test_data/claude-code-*.md |
| Anthropic 公司 | 40 | test_data/anthropic-company/*.md |


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


