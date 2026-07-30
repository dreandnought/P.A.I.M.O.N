# Ontology 构建与自进化方案（基于 SkillOpt 范式优化）

> 创建时间：2026-07-11
> 基于：SkillOpt 文本空间优化器（微软 arXiv:2605.23904）+ PRD Ontology MCP 现有方案
> 状态：计划阶段

---

## 一、核心设计思想

将 SkillOpt 的深度学习训练范式完整映射到 Ontology 的构建和自进化过程中：

| SkillOpt 概念 | Ontology 对应 | 说明 |
|:---|:---|:---|
| 权重 θ（技能文档） | Ontology 存储（entities + relations 表） | 可优化的"参数" |
| 损失函数 L | 1 - Ontology 质量得分 | 覆盖率、准确率、Agent 满意度 |
| 梯度 ∇L | LLM 抽取的候选实体/关系 + 审核反馈 | 结构化的更新提案 |
| 学习率 α | 更新预算 Lt（每轮最多入库多少条） | 控制 Ontology 变更步长 |
| Batch size | 一次解析的 PRD 批量 | 证据收集单元 |
| Mini-batch | 审核小批量（每批审核几条） | 减少 LLM 抽取方差 |
| Epoch | 一轮完整的 PRD 解析->审核->入库->验证循环 | 完整训练轮次 |
| 验证门控 | 审核确认（必须人工通过才入库） | 防止噪声进入 Ontology |
| 拒绝缓冲区 | 审核拒绝记录 + 错误模式积累 | 避免重复犯同样错 |
| 慢更新 | Epoch 级 Ontology 质量评估 | 纵向精炼，发现长期模式 |
| 元技能 | 审核经验库（LLM prompt 持续优化） | 优化器侧记忆 |
| 部署推理 | Agent 通过 MCP 查询 Ontology | 训练与部署解耦 |

---

## 二、完整训练循环

```
┌──────────────────────────────────────────────────────────────────┐
│                  Ontology 自进化训练循环                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  初始化: 空 Ontology, 公司先验知识, 行业 Schema 模板               │
│                                                                  │
│  FOR epoch = 1 TO N:                                             │
│    FOR step = 1 TO steps_per_epoch:                              │
│                                                                  │
│      ① ROLLOUT（前向传播 - 抽取）                                 │
│         LLM 用当前 Ontology + 公司知识解析 PRD 批量               │
│         -> 产生候选实体/关系 + LLM 自评置信度                      │
│                                                                  │
│      ② REFLECT（反向传播 - 反思）                                 │
│         候选结果按高/低置信度分组                                  │
│         LLM 分析: 高置信度但与已有 Ontology 矛盾的 -> 标记重点审核 │
│                   低置信度但可能是隐性关系 -> 标记潜在价值         │
│         -> 生成审核建议（优先级排序）                              │
│                                                                  │
│      ③ AGGREGATE（梯度合并 - 聚合）                               │
│         候选实体/关系去重、消歧、与已有 Ontology 对齐              │
│         合并同一 PRD 内的多轮抽取结果                             │
│         -> 统一的候选池                                           │
│                                                                  │
│      ④ SELECT（梯度裁剪 - 筛选）                                  │
│         按优先级排名，裁剪至 Lt 条（学习率调度）                   │
│         高置信度 + 有原文证据 -> 高优先级                          │
│         低置信度 + 无矛盾 -> 中优先级                              │
│         低置信度 + 与已有 Ontology 矛盾 -> 低优先级                │
│         -> 选定的候选集                                           │
│                                                                  │
│      ⑤ GATE（验证门控 - 人工审核）                                │
│         人工审核候选集                                            │
│         IF 审核通过:                                             │
│           confidence = 1.0, status = 'confirmed'                 │
│           入库，更新 recency                                      │
│         IF 审核拒绝:                                              │
│           记录到拒绝缓冲区，标记错误模式                           │
│         IF 审核修改:                                              │
│           修改后 confidence = 0.8, status = 'edited'             │
│           入库                                                   │
│                                                                  │
│    END FOR                                                       │
│                                                                  │
│    EPOCH 结束: 慢更新 + 元技能更新                                 │
│    ⑥ SLOW UPDATE: 对比前后 Ontology 质量变化                      │
│       改进的关系: 提升权重                                        │
│       退化的关系: 降权并标记                                      │
│       持续未验证的关系: 衰减                                      │
│       稳定确认的关系: 固化                                        │
│    ⑦ META SKILL: 更新审核经验库                                   │
│       总结本 epoch 的拒绝模式                                     │
│       更新 LLM 抽取 prompt 中的规则补充                           │
│                                                                  │
│  END FOR                                                         │
│                                                                  │
│  输出: confirmed Ontology (SQLite)                               │
│  部署: Agent 通过 MCP 查询，零额外 LLM 开销                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、各阶段详细设计

### 3.1 ROLLOUT（前向传播 - 抽取阶段）

**对应 SkillOpt**：目标模型用当前技能执行任务，产生轨迹

**我们的对应**：LLM 用当前 Ontology 作为上下文，解析新 PRD

#### 输入

```python
extract(
    prd_text,                    # 新 PRD 文本
    company_context,             # 公司知识（定位、组织、经营状况）
    existing_ontology_summary,   # 当前 Ontology 的摘要（已有实体列表）
    industry_schema,             # 行业 Schema 模板
    rejection_patterns,          # 上一轮积累的拒绝模式（元技能）
)
```

#### 改进点：公司知识注入

当前 `llm_extractor.py` 只接受 `prd_text` 一个参数。改为：

```python
class PRDExtractor:
    def extract(
        self,
        prd_text: str,
        company_context: dict = None,      # 新增
        existing_entities: list = None,      # 新增
        rejection_patterns: list = None,    # 新增
    ) -> dict:
        """
        company_context: {
            "company_name": "XX公司",
            "business": "SaaS 协同办公",
            "org_structure": [{"team": "支付团队", "owns": ["支付模块", "订单模块"]}],
            "current_focus": "Q3 重点是商业化变现",
            "tech_stack": "Python + Vue + Kubernetes",
        }
        existing_entities: ["支付模块", "用户系统", "权限管理"]  # 已有 Ontology 实体
        rejection_patterns: [
            "之前 30% 的拒绝因为实体边界不清 -> 请精确划分",
            "之前 20% 的拒绝因为关系无原文支持 -> 必须有原文证据",
        ]
        """
```

#### System Prompt 增强

```python
SYSTEM_PROMPT = f"""
你是一个 PRD 需求分析专家。请从 PRD 文档中提取结构化的需求实体和关系。

实体类型:
{ENTITY_TYPE_DESC}

关系类型:
{RELATION_TYPE_DESC}

{company_context_section}       # 公司知识

{existing_ontology_section}     # 已有 Ontology（用于消歧和对齐）

{rejection_patterns_section}    # 审核经验（元技能）

抽取规则:
1. 实体名称使用 PRD 中的原文，不要概括或重命名
2. 每个实体必须指明其类型
3. 关系必须基于 PRD 原文中的明确表述，不要推理隐性关系
4. 置信度低于 0.6 的关系不要输出
5. 输出必须为合法的 JSON 格式
6. 如果公司知识中已有同名实体，请标注 resolved_to
"""
```

### 3.2 REFLECT（反向传播 - 反思阶段）

**对应 SkillOpt**：将轨迹分为失败/成功组，识别可重复的模式

**我们的对应**：LLM 对候选结果自评，识别需要重点审核的部分

#### 设计

```python
def reflect_on_extraction(
    candidates: dict,           # 抽取的实体/关系
    existing_ontology: dict,     # 已有 Ontology
    prd_text: str,              # 原始 PRD
) -> dict:
    """
    返回审核建议：
    {
        "high_priority": [   # 需要重点审核（与已有 Ontology 矛盾）
            {"item": "...", "reason": "与已有关系冲突", "conflict_detail": "..."}
        ],
        "medium_priority": [ # 建议审核（新增实体/关系）
            {"item": "...", "reason": "新实体", "similar_entities": ["..."]}
        ],
        "low_priority": [    # 可以快速通过（高置信度 + 有原文证据）
            {"item": "...", "reason": "高置信度且有明确原文"}
        ],
    }
    """
```

### 3.3 AGGREGATE（梯度合并 - 聚合阶段）

**对应 SkillOpt**：分层合并编辑提案，过滤重复/矛盾

**我们的对应**：候选实体/关系的去重、消歧、对齐（已有 `postprocessor.py`，需增强）

#### 改进点

当前 `postprocessor.py` 的 `postprocess()` 函数已有去重和消歧。需要增加：

```python
def postprocess(
    result: dict,
    existing_entities: list = None,    # 已有 Ontology 实体
    company_entities: list = None,     # 公司知识实体（新参数）
    confidence_threshold: float = 0.6,
) -> dict:
    """
    新增逻辑：
    1. 与公司知识实体消歧（如 PRD 中的"支付" -> 公司的"支付模块"）
    2. 保留来源追踪（哪条关系来自哪段 PRD 原文）
    3. 标注"匹配类型"：exact / alias / fuzzy / new
    """
```

### 3.4 SELECT（梯度裁剪 - 筛选阶段）

**对应 SkillOpt**：按效用排名，裁剪至 Lt 个编辑

**我们的对应**：按优先级排名，控制每轮入库数量

#### 学习率调度器

```python
class OntologyLRScheduler:
    """Ontology 更新的学习率调度器"""

    def __init__(self, mode="cosine", max_lr=10, min_lr=2, total_epochs=10):
        self.mode = mode
        self.max_lr = max_lr      # 每轮最多入库 10 条
        self.min_lr = min_lr       # 最少 2 条
        self.total_epochs = total_epochs

    def compute_lr(self, epoch: int) -> int:
        """计算当前 epoch 的更新预算"""
        if self.mode == "constant":
            return self.max_lr
        elif self.mode == "linear":
            t = epoch / self.total_epochs
            return max(self.min_lr, round(self.max_lr + (self.min_lr - self.max_lr) * t))
        elif self.mode == "cosine":
            t = min(epoch, self.total_epochs) / self.total_epochs
            lr = self.min_lr + 0.5 * (self.max_lr - self.min_lr) * (1 + math.cos(math.pi * t))
            return max(self.min_lr, round(lr))
```

#### 优先级排名

```python
def rank_candidates(candidates: list, existing_ontology: dict) -> list:
    """按优先级对候选实体/关系排名"""
    for item in candidates:
        score = 0
        # 高置信度 +2
        score += item.get("confidence", 0) * 2
        # 有原文证据 +1
        if item.get("evidence"):
            score += 1
        # 与已有 Ontology 匹配（巩固已有知识）+1
        if item.get("resolution") == "exact":
            score += 1
        # 与已有 Ontology 矛盾（需要审核）-1 但标记高优先级
        if item.get("conflict"):
            score -= 1
            item["review_priority"] = "high"
        # 公司知识中已有（确定性来源）+2
        if item.get("source") == "company_knowledge":
            score += 2
        item["rank_score"] = score
    return sorted(candidates, key=lambda x: x["rank_score"], reverse=True)
```

### 3.5 GATE（验证门控 - 人工审核）

**对应 SkillOpt**：候选技能必须严格优于当前分数才接受

**我们的对应**：人工审核确认后才入库，拒绝的不入库但记录

#### 审核工作流

```python
def review_gate(
    candidate: dict,
    reviewer_action: str,        # approve / reject / edit
    reviewer_id: str,
    edit_corrections: dict = None,
) -> dict:
    """
    审核门控决策
    """
    if reviewer_action == "approve":
        candidate["confidence"] = 1.0
        candidate["status"] = "confirmed"
        candidate["verified_by"] = reviewer_id
        candidate["verified_at"] = datetime.now()
        # 入库

    elif reviewer_action == "reject":
        candidate["confidence"] = 0.1
        candidate["status"] = "rejected"
        # 不入库，但记录到 rejected_relations 表
        log_rejection(candidate, reviewer_id)

    elif reviewer_action == "edit":
        # 应用修改
        candidate.update(edit_corrections)
        candidate["confidence"] = 0.8
        candidate["status"] = "edited"
        candidate["verified_by"] = reviewer_id
        # 入库

    return candidate
```

#### 审核界面（MCP Tool）

```python
# MCP 工具列表
@mcp.tool()
def list_pending_reviews(limit: int = 20) -> str:
    """列出待审核的实体/关系，按优先级排序"""

@mcp.tool()
def approve_item(item_id: str, item_type: str) -> str:
    """审核通过一个实体或关系"""

@mcp.tool()
def reject_item(item_id: str, reason: str, error_pattern: str = "") -> str:
    """拒绝一个实体或关系，记录原因和错误模式"""

@mcp.tool()
def edit_and_approve(item_id: str, corrections: dict) -> str:
    """修改并确认"""

@mcp.tool()
def batch_review(action: str, item_ids: list[str]) -> str:
    """批量审核（全部通过/全部拒绝）"""
```

### 3.6 SLOW UPDATE（慢更新 - Epoch 级评估）

**对应 SkillOpt**：每个 epoch 结束时比较前后技能，分类为改进/退化/持续失败/稳定成功

**我们的对应**：定期评估 Ontology 整体质量，发现长期模式

#### 四组分类分析

```python
def slow_update_evaluation(
    prev_ontology: dict,     # 上一 epoch 的 Ontology 快照
    curr_ontology: dict,     # 当前 Ontology
    agent_usage_log: list,   # Agent 查询日志
    dev_feedback: list,       # 开发反馈（Bug、Code Review）
) -> dict:
    """
    四组分类：
    - improved: 之前未确认 -> 现在确认（关系被验证）
    - regressed: 之前确认 -> 现在发现问题（关系被推翻）
    - persistent_failure: 多次审核拒绝的模式
    - stable_success: 多次确认正确的关系
    """
    results = {
        "improved": [],           # 提升权重
        "regressed": [],          # 降权并标记
        "persistent_failure": [],  # 移入"高风险模式"库
        "stable_success": [],     # 固化为 confidence=1.0
    }

    for relation in curr_ontology["relations"]:
        prev_state = find_in_prev(relation, prev_ontology)

        if prev_state is None and relation["status"] == "confirmed":
            results["improved"].append(relation)
        elif prev_state and prev_state["status"] == "confirmed" and relation["status"] == "rejected":
            results["regressed"].append(relation)
        elif prev_state and prev_state["reject_count"] >= 3:
            results["persistent_failure"].append(relation)
        elif relation["verify_count"] >= 3 and relation["status"] == "confirmed":
            results["stable_success"].append(relation)

    return results
```

#### 权重更新动作

| 分类 | 动作 |
|------|------|
| improved | confidence += 0.1（上限 1.0），recency 重置 |
| regressed | confidence -= 0.3，标记 `needs_recheck` |
| persistent_failure | 标记为"高风险模式"，加入 LLM prompt 的 rejection_patterns |
| stable_success | confidence = 1.0（固定），不再衰减 |

### 3.7 META SKILL（元技能 - 审核经验库）

**对应 SkillOpt**：总结编辑模式的成功与失败，为未来优化器调用提供上下文

**我们的对应**：积累审核经验，持续优化 LLM 抽取的 prompt

#### 设计

```python
class MetaSkillUpdater:
    """审核经验库更新器"""

    def update(self, epoch_experiences: list) -> str:
        """
        从本 epoch 的审核记录中提取模式，更新元技能
        """
        # 统计拒绝原因分布
        rejection_stats = self._analyze_rejections(epoch_experiences)
        # {实体边界不清: 30%, 关系无原文: 20%, 实体名称不一致: 15%, ...}

        # 生成规则补充
        new_rules = self._generate_rules(rejection_stats)

        # 更新元技能文档
        meta_skill = self._load_current()
        meta_skill = self._merge_rules(meta_skill, new_rules)
        self._save(meta_skill)

        return meta_skill

    def get_prompt_section(self) -> str:
        """返回拼入 LLM prompt 的元技能片段"""
        meta_skill = self._load_current()
        if not meta_skill:
            return ""
        return f"\n审核经验（请避免以下错误模式）:\n{meta_skill}\n"
```

#### 元技能存储

```sql
CREATE TABLE meta_skill (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,       -- 规则补充文本
    rejection_stats TEXT NOT NULL,      -- JSON: 拒绝原因统计
    epoch           INTEGER NOT NULL,   -- 第几轮 epoch
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 四、新增数据模型

### 4.1 审核相关表

```sql
-- 审核队列
CREATE TABLE review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type       TEXT NOT NULL CHECK(item_type IN ('entity', 'relation')),
    item_data       TEXT NOT NULL,      -- JSON: 候选数据
    item_source     TEXT NOT NULL,      -- prd_id / company_knowledge / code_analysis
    priority        TEXT NOT NULL CHECK(priority IN ('high', 'medium', 'low')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'approved', 'rejected', 'edited')),
    reviewer_id     TEXT,
    review_note     TEXT,
    error_pattern   TEXT,               -- 拒绝时的错误模式归类
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at     TEXT
);

-- 拒绝记录（拒绝缓冲区）
CREATE TABLE rejected_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type       TEXT NOT NULL,
    item_data       TEXT NOT NULL,      -- 被拒绝的数据
    reason          TEXT NOT NULL,
    error_pattern   TEXT,               -- 错误模式归类
    prd_id          TEXT,
    epoch           INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 权重更新日志
CREATE TABLE weight_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type     TEXT NOT NULL CHECK(target_type IN ('entity', 'relation')),
    target_id       TEXT NOT NULL,
    weight_before   REAL NOT NULL,
    weight_after    REAL NOT NULL,
    reason          TEXT NOT NULL,      -- review_approve / review_reject / code_verify / decay / slow_update
    epoch           INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 4.2 元技能表

```sql
-- 元技能（审核经验库）
CREATE TABLE meta_skill (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    rejection_stats TEXT NOT NULL,
    epoch           INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 当前生效的元技能版本
CREATE TABLE meta_skill_current (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    version         INTEGER NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 4.3 Ontology 快照（用于慢更新对比）

```sql
-- Ontology 快照（每个 epoch 结束时保存）
CREATE TABLE ontology_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch           INTEGER NOT NULL,
    snapshot_data   TEXT NOT NULL,      -- JSON: 实体和关系快照
    stats           TEXT NOT NULL,      -- JSON: 统计信息
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 五、学习率调度策略

### 5.1 默认配置

```yaml
# llm_config.yaml 新增
ontology_training:
  # 学习率调度
  lr_scheduler: "cosine"        # constant | linear | cosine
  max_lr: 10                    # 每轮最多入库 10 条（冷启动期允许较多）
  min_lr: 2                     # 稳定期最少 2 条
  total_epochs: 10              # 预期总 epoch 数

  # Batch 配置
  rollout_batch: 1              # 每次解析 1 份 PRD
  review_minibatch: 5           # 每批审核 5 条候选

  # 验证门控
  gate_mode: "strict"           # strict（必须人工审核） | auto（高置信度自动通过）
  auto_approve_threshold: 0.95  # auto 模式下自动通过的阈值

  # 慢更新
  slow_update_enabled: true
  stable_success_threshold: 3   # 确认 3 次以上固化

  # 权重衰减
  decay_period_days: 90         # 90 天未验证开始衰减
  decay_floor: 0.1              # 衰减下限
```

### 5.2 不同阶段的调度建议

| 阶段 | 调度器 | max_lr | min_lr | 说明 |
|------|-------|--------|--------|------|
| 冷启动（epoch 1-3） | constant | 10 | - | 大步快速建立基础 Ontology |
| 成长期（epoch 4-7） | cosine | 8 | 4 | 逐步收窄，精化 |
| 稳定期（epoch 8+） | cosine | 4 | 2 | 微调模式，防止震荡 |

---

## 六、实现优先级

### 第一批（打通核心闭环）

| 序号 | 任务 | 依赖 |
|------|------|------|
| 1 | SQLite 存储层（entities + relations 带 status/weight 字段） | 无 |
| 2 | 审核队列表 + 审核工作流 | 1 |
| 3 | LLM 抽取增强（支持 company_context + existing_entities 参数） | 无 |
| 4 | 正式 MCP Server（接入 Parser + Storage） | 1, 3 |
| 5 | 基础审核 MCP Tools（list_pending, approve, reject, edit） | 2, 4 |

### 第二批（SkillOpt 范式落地）

| 序号 | 任务 | 依赖 |
|------|------|------|
| 6 | 优先级排名 + 学习率调度器 | 2 |
| 7 | 拒绝缓冲区 + 错误模式记录 | 2 |
| 8 | 元技能表 + prompt 自动更新 | 7 |
| 9 | 查询 MCP Tools（query, trace_impact, list_constraints） | 4 |

### 第三批（自进化闭环）

| 序号 | 任务 | 依赖 |
|------|------|------|
| 10 | Ontology 快照 + 慢更新评估 | 1, 2 |
| 11 | 权重自动衰减（定时任务） | 1 |
| 12 | Agent 使用统计反馈 | 4 |
| 13 | 代码静态分析建关系 | 4 |
| 14 | 自监督反馈（开发结果 -> 验证信号） | 10 |

---

## 七、与原方案的差异

### 原方案的自监督框架（wiki 文档中）

```
Phase 1: 初始化 -> Phase 2: PRD 验证 -> Phase 3: 自监督学习 -> Phase 4: Ontology 更新
```

### 优化后的框架（引入 SkillOpt 范式）

| 原方案 | 优化后 | 改进点 |
|--------|--------|--------|
| Phase 1 初始化 | ROLLOUT + 公司知识注入 | 不是空冷启动，有先验知识 |
| Phase 2 PRD 验证 | GATE 人工审核（验证门控） | 明确的审核工作流，不是"自然验证" |
| Phase 3 自监督学习 | SLOW UPDATE 四组分类 | 结构化的改进/退化/持续失败/稳定成功分类 |
| Phase 4 Ontology 更新 | SELECT + 权重更新 | 有学习率控制和优先级排名 |
| 无 | REFLECT 反思阶段 | 新增：LLM 自评 + 审核优先级建议 |
| 无 | META SKILL 元技能 | 新增：审核经验积累，prompt 持续优化 |
| 无 | 拒绝缓冲区 | 新增：避免重复犯同样错误 |

**核心改进**：原方案的自监督框架是概念性的（"预测正确则强化，错误则修正"），优化后变成了**有明确工程实现路径的、可量化的训练循环**。
