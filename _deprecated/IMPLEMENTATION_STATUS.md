# PRD Ontology MCP — 实现状态记录

> 最后更新：2026-07-11 23:10
> 当前阶段：Phase 1 (MVP) — 约 70% 完成

---

## 一、已完成模块

### 1. Parser 解析器（`parser/`）

#### 1.1 Schema 定义（`parser/schema.py`）

**功能：** 实体类型和关系类型的枚举定义。

- **7 种实体类型：** requirement, function, module, interface, data_entity, constraint, actor
- **8 种关系类型：** depends_on, implements, constrains, causes, conflicts_with, refines, contains, relates_to
- 提供 LLM 抽取用的 Schema 描述文本，拼接成 prompt 的一部分

#### 1.2 预处理层（`parser/preprocessor.py`）

**功能：** 将各种格式的 PRD 统一为结构化 Markdown，供 LLM 抽取使用。

核心函数：

| 函数 | 作用 |
|------|------|
| `normalize_prd(text, source_format)` | 主入口。自动检测格式（markdown/plain），统一转为结构化 Markdown |
| `_detect_format(text)` | 通过正则检测是否有 Markdown 标题，判断格式 |
| `_clean_markdown(text)` | 移除 HTML 注释、规范化空行 |
| `_plain_to_markdown(text)` | 将纯文本转换为简单 Markdown（全大写行→标题，数字序号行→三级标题） |
| `_remove_noise(text)` | 移除目录、修订记录、页码等非内容部分 |
| `extract_sections(text)` | 按标题层级分段，返回嵌套的章节结构 `[{level, title, content, children}]` |
| `section_to_text(sections, max_chars)` | 将结构化章节转回文本（支持截断），用于 LLM 输入 |

#### 1.3 LLM 抽取层（`parser/llm_extractor.py`）

**功能：** 使用 LLM API（目前配置为 SiliconFlow DeepSeek V4 Flash）从 PRD 中提取实体和关系。

**核心架构：** 两阶段抽取

```
Phase 1: 实体抽取
  输入: PRD 文本
  输出: [{name, type, description}, ...]
  约束: 名称用 PRD 原文，不要概括重命名

Phase 2: 关系抽取
  输入: PRD 文本 + Phase 1 输出的实体列表
  输出: [{source, type, target, confidence, evidence}, ...]
  约束: source/target 必须是已知实体，只抽取明确表述，置信度<0.6 不输出
```

**类结构：**

| 类/方法 | 说明 |
|---------|------|
| `PRDExtractor.__init__()` | 初始化 API key、model、temperature |
| `PRDExtractor.extract(prd_text)` | 主入口，依次调用两阶段抽取 |
| `PRDExtractor._extract_entities(prd_text)` | Phase 1：发送实体抽取 prompt，解析 JSON 响应 |
| `PRDExtractor._extract_relations(prd_text, entities)` | Phase 2：发送关系抽取 prompt，限制实体范围 |
| `PRDExtractor._demo_extract(prd_text)` | 无 API Key 时的演示模式，返回模拟数据 |
| `extract_prd(prd_text, api_key)` | 便捷函数，自动读取配置后调用 |

**配置加载方式：**
- 从 `llm_config.yaml` 读取 provider、model、temperature
- 支持环境变量覆盖（`LLM_API_KEY`, `DEEPSEEK_API_KEY` 等）
- 当前配置：SiliconFlow 平台，DeepSeek V4 Flash 模型

#### 1.4 后处理层（`parser/postprocessor.py`）

**功能：** 对 LLM 抽取结果进行去重、消歧、置信度校准。

核心函数：

| 函数 | 作用 |
|------|------|
| `deduplicate_entities(entities, threshold=0.85)` | 基于 rapidfuzz 模糊匹配去重合并实体 |
| `deduplicate_relations(relations)` | 同类型同方向关系去重，保留高置信度 |
| `filter_low_confidence(items, threshold=0.6)` | 过滤低置信度项 |
| `resolve_with_ontology(entities, existing_entities, threshold=0.8)` | 与已有 Ontology 中的实体做消歧匹配（精确/别名/模糊三级） |
| `postprocess(result, existing_entities, confidence_threshold)` | 完整后处理流程：过滤→实体去重→关系去重→Ontology消歧 |

**后处理流程：**
```
原始抽取结果
    ↓ 过滤低置信度关系（<0.6）
    ↓ 实体名称模糊去重（相似度≥0.85）
    ↓ 关系去重（同 type+source+target 保留高置信度）
    ↓ 与已有 Ontology 消歧（精确→别名→模糊）
最终输出
```

#### 1.5 Pipeline（`parser/pipeline.py`）

**功能：** 组合预处理、LLM 抽取、后处理的完整流程。

```python
parse_prd(raw_text, api_key=None, existing_entities=None, source_format="auto")
    → {entities, relations, sections, stats, linked, new_entities}
```

返回的 stats 包含：输入字符数、规范化后字符数、章节数、实体数、关系数、已关联实体数、新建实体数。

#### 1.6 LLM 配置加载器（`parser/config.py`）

**功能：** 从 `llm_config.yaml` 读取 LLM 配置，支持多 provider 和环境变量覆盖。

支持的 provider 配置方式：
- `default_provider` 指定默认
- `providers` 段定义各 provider 的 api_key/base_url/models
- `prd_parser` 和 `prd_generator` 段可引用 provider 配置
- `get_llm_config(section)` 自动解析变量引用（如 `${default_provider}`）
- 环境变量优先级高于配置文件

#### 1.7 LLM 配置文件（`llm_config.yaml`）

配置了三个 provider：

| Provider | 用途 | 模型 |
|----------|------|------|
| siliconflow | 默认（国内直连） | deepseek-ai/DeepSeek-V4-Flash |
| ark | 火山引擎 | deepseek-v4-flash |
| openai | 备用 | gpt-4o |

PRD 解析器配置：temperature=0.1（低温度确保抽取稳定性），两阶段抽取模式。

---

### 2. PRD 生成器（`tools/prd_generator.py`）

**功能：** 将用户的需求描述转为结构化 PRD 文档。

- 系统 Prompt 定义了 PRD 的标准章节结构（背景、产品概述、功能需求、非功能需求、数据实体、约束、指标）
- 调用 LLM 生成完整 Markdown PRD
- 支持保存到 `prd_samples/` 目录

---

### 3. MCP Hello Server（`hello_server.py`）

**功能：** MCP 服务器原型，验证 MCP 协议集成。

暴露了 3 个 demo 工具：

| 工具 | 说明 |
|------|------|
| `greet(name)` | 测试 MCP 连接是否正常 |
| `parse_prd_demo(title, content)` | 模拟 PRD 解析功能，返回假数据 |
| `query_ontology(entity_name)` | 模拟 Ontology 查询，返回假数据 |

**⚠️ 注意：** 这只是一个演示服务器，没有接入真正的 Parser 和 Storage，所有数据都是硬编码模拟的。

---

### 4. 测试脚本（`test_parser.py`）

**功能：** 端到端测试 Parser 模块。

- 内置了一份「用户登录功能 PRD」示例文档（包含背景、功能需求、非功能需求、模块、数据实体、约束）
- 运行 `parse_prd()` 完整流程
- 输出统计信息、实体列表、关系列表、章节结构
- 可在无 API Key 的情况下以演示模式运行

---

## 二、模块依赖关系图

```
hello_server.py (MCP Server - 演示)
    │ 依赖 parser.pipeline
    ▼
parser/pipeline.py (完整 Pipeline)
    ├── parser/preprocessor.py (文本标准化)
    ├── parser/llm_extractor.py (LLM 抽取)
    │   ├── parser/schema.py (类型定义)
    │   └── parser/config.py (配置加载)
    │       └── llm_config.yaml (配置文件)
    └── parser/postprocessor.py (去重/消歧)

tools/prd_generator.py (PRD 生成 - 独立)
    └── parser/config.py (复用配置加载)

test_parser.py (测试)
    └── parser/pipeline.py
```

---

## 三、未完成的核心缺口

| 缺口 | 说明 | 优先级 |
|------|------|--------|
| **SQLite 存储层** | 未实现。需要实现 entities, relations, documents, entity_history 等 8 张表及 CRUD | 🔴 高 |
| **MCP 正式服务器** | 当前只有 hello 演示，未连接真正的 Parser 和 Storage | 🔴 高 |
| **冲突检测引擎** | `engine/conflict.py` 未实现 | 🟡 中 |
| **影响分析引擎** | `engine/impact.py`（BFS 图遍历）未实现 | 🟡 中 |
| **自监督学习** | `engine/feedback.py` 未实现 | 🟢 低 |
| **代码静态分析** | 从代码模块结构反向推导依赖关系 | 🟢 低 |

---

## 四、当前项目文件结构

```
PRDOntology/
├── hello_server.py          # MCP 演示服务器
├── llm_config.yaml          # LLM 统一配置
├── test_parser.py           # 解析器测试脚本
├── parser/
│   ├── __init__.py
│   ├── config.py            # LLM 配置加载器
│   ├── preprocessor.py      # 预处理（格式统一、章节提取）
│   ├── schema.py            # 实体/关系类型定义
│   ├── llm_extractor.py     # LLM 两阶段抽取
│   ├── postprocessor.py     # 后处理（去重、消歧）
│   └── pipeline.py          # 完整 Pipeline
├── tools/
│   └── prd_generator.py     # PRD 生成器
├── venv/                    # Python 虚拟环境
└── prd_samples/             # 生成的 PRD 样本（空）
```
