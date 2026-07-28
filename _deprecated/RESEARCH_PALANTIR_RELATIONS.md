# Palantir Ontology 关系挖掘、检索与前向推理机制调研

> 调研时间：2026-07-11
> 数据来源：Palantir 官方文档（palantir.com/docs）、Palantir 架构中心、Grokipedia、GitHub 开源工具分析

---

## 一、核心概念：Link（关系）在 Palantir 中的定位

Palantir Ontology 中，关系被称为 **Link**。它是连接两个 **Object**（对象实例）的桥梁，是整个 Ontology 语义层的核心组件。

### 1.1 Link Type vs Link（模式 vs 实例）

Palantir 严格区分两个层次：

| 概念 | 类比 | 说明 |
|------|------|------|
| **Link Type** | 关系类型的 Schema 定义 | 如 `Employee → Company` 的 `Employer` 关系类型 |
| **Link** | 单个关系实例 | 如 "Melissa Chang → Acme, Inc." 这一条具体关系 |

这与数据库表 Schema 和行的关系类似。Link Type 定义的是元数据（Metadata），Link 是数据（Data）。

### 1.2 Link 的三层设计

```
Ontology Manager 中定义 Link Type
    ↓
通过 Pipeline / Data Mapping 建立 Links（数据驱动）
    ↓
Ontology Engine 中 Links 被索引化存储，支持高效查询和遍历
```

**关键点：Link Type 的定义和 Link 的建立是分离的。**

- **Link Type** 在 Ontology Manager 中手动定义（Schema 层）
- **Links** 是从底层数据源自动映射出来的（数据层），不需要手动创建每条关系

---

## 二、关系挖掘与建立（关系发现）

### 2.1 核心原则：数据驱动，非 LLM 驱动

**Palantir 的关系建立不是靠 LLM 抽取，而是靠结构化数据源的映射。**

Palantir 的 Link 建立方式有三种：

#### 方式 A：外键关系类型（Foreign Key）

```
Object Type A          Object Type B
    │                       │
    │  property_A  ←─────  property_B (FK)
    │                       │
    └──────── 一对多/一对一 ──┘
```

- 在 Object Type A 上选择一个 Property 作为外键
- 在 Object Type B 上选择一个 Property 作为主键
- Palantir 自动为每条数据建立 Link
- 支持 one-to-one 和 many-to-one 基数

#### 方式 B：连接表关系类型（Join Table）

```
Object Type A          Join Table          Object Type B
    │                       │                    │
    │  id_A  ←───────  fk_A + fk_B ────────→  id_B
    │                       │                    │
    └──────────── 多对多 ────────────────────────┘
```

- 使用一个中间数据集（Join Table）来关联两个 Object Type
- 支持 many-to-many 基数
- Join Table 本身是一个 Foundry Dataset

#### 方式 C：Backing Object 关系类型

- 用于同一底层数据集内的自关联
- 如 `Employee` 和 `Manager` 是同一个 Object Type 的不同实例

### 2.2 关系建立的流程

```
步骤 1: 数据入湖（Pipeline Builder/ETL）
    ↓ 将结构化数据（CSV、数据库表、API 输出等）引入 Foundry
步骤 2: 创建 Object Type
    ↓ 定义 Schema（Object Type 的 Property、Primary Key、Title Key）
步骤 3: 创建 Link Type
    ↓ 选择关系类型（FK/Join Table/Backing Object）+ 配置映射
步骤 4: Pipeline 输出到 Ontology
    ↓ 数据流从 Pipeline 写入 Object Storage
步骤 5: Links 自动生成
    ↓ 基于映射规则，每条数据自动建立 Link 实例
```

### 2.3 与我们的方案的关键差异

| 维度 | Palantir | 我们的方案 |
|------|----------|-----------|
| **关系发现方式** | 从结构化数据映射（确定性） | LLM 从非结构化 PRD 抽取（概率性） |
| **关系 Schema** | 手动定义 Link Type | 预定义 8 种关系类型 |
| **关系实例生成** | 自动基于 FK/Join Table | LLM 两阶段抽取 |
| **准确性** | 100%（数据源自带） | 依赖 LLM 质量 |
| **关系类型** | 语义化命名（如 `employer`, `assigned_aircraft`） | 通用类型（depends_on, implements 等） |

**核心发现：** Palantir 的关系建立本质上是**确定性数据映射**，不是"挖掘"或"发现"。它假设底层数据源已经包含了外键关系，Ontology 只是把这些关系以语义化的方式暴露出来。

**对我们方案的启示：** 我们面临一个本质上更难的问题——从非结构化文本中挖掘结构化关系。这也说明我们的自监督反馈机制是必要的，因为 LLM 抽取天然存在不确定性。

### 2.4 自动化关系发现工具（PalantirOntologyGenerator）

GitHub 上的开源工具 `jaymd96/PalantirOntologyGenerator` 提供了一个参考实现：

```python
# CSV → Schema → FK 分析 → Object Type + Link Type 自动生成
# 核心逻辑：分析 CSV 的列名、数据类型、主键/外键关系
# 自动推断 FK 关系（列名匹配、数据类型匹配等）
```

这个工具说明 Palantir 生态中关系发现的自动化程度：**通过结构化数据分析自动推断 FK 关系**，但仍是在结构化数据的框架内。

---

## 三、关系检索与查询

### 3.1 Ontology Engine 的读取架构

Palantir 的查询引擎支持三种读取模式：

```
SQL 查询（高吞吐、批量）
    │
实时订阅（低延迟、推送）
    │
各种物化方式（Materialized Views、派生属性等）
```

#### 关键特性：模块化读取架构

- Object 和 Link 被索引化存储，关系查询不需要图数据库
- 使用自研分布式索引层，而非 Neo4j 等原生图数据库
- **Links 被存储为 Object 的属性**（索引化的外键），遍历时通过索引层快速跳转

### 3.2 检索方式

#### 方式 1：Object Views（发现模式）

- 用户通过搜索框查找 Object
- 支持自由文本搜索、Link 遍历搜索、下钻搜索
- 每个 Object Type 有预定义的 360 度视图

#### 方式 2：Vertex（图探索模式）

Vertex 是 Palantir 的图可视化工具，专门用于关系探索：

```
用户选择一个或多个 Object
    ↓
"Search Around" 功能：自动展示相邻的 Link
    ↓
用户可以逐层展开（多跳遍历）
    ↓
多步 Search Around：设置过滤条件，跨 Object Type 遍历
```

**技术实现：** Vertex 的 Search Around 是 Palantir 关系检索的核心 UX 模式。它展示从当前 Object 出发的所有 Link，用户可以选择展开哪些 Link，以及是否添加过滤条件。多步 Search Around 允许用户跨多个 Object Type 进行复杂的路径查询。

#### 方式 3：OSDK（程序化查询）

通过 Palantir 的 Object SDK（OSDK），开发者可以在代码中查询关系：

```python
# OSDK 示例（概念性）
employee = client.objects.Employee.get("melissa_chang")
company = employee.links.employer  # Link 遍历
```

OSDK 自动为每个 Object Type 生成类型安全的客户端代码，包括 Link 遍历方法。

### 3.3 查询的性能特点

- **非原生图数据库**：Palantir 的 Links 不是用 Neo4j 或任何专用图数据库存储的
- **索引层遍历**：关系查询通过分布式索引层实现，类似"邻接表"的查询模式
- **Spark 分布式计算**：大规模关系分析（如全图遍历）依赖 Spark 分布式计算

---

## 四、前向推理

### 4.1 什么是 Palantir 的"推理"

Palantir 的 Ontology 推理不是传统的 OWL/SHACL 推理，而是**基于 Function 的编程式推理**。

#### Functions（函数）

Functions 是 Palantir 中嵌入业务逻辑的核心机制：

```
Function
    ├── 读取 Object 和 Links
    ├── 执行业务逻辑（简单规则 ~ 复杂 ML 模型）
    ├── 可调用 LLM、优化器、传统 ML 模型
    └── 返回计算结果或触发 Action
```

Functions 可以：
- **遍历 Links**：从 Object A 出发，沿 Link 找到 Object B，再继续找到 Object C
- **执行计算**：聚合、统计、预测
- **调用外部系统**：LLM API、ML 模型服务、优化求解器

#### Action Types（行动类型）

Actions 是 Functions 的执行容器：

```
Action
    ├── 前置校验（Pre-validation）
    ├── Function 执行
    ├── 权限检查
    └── 写回源系统
```

### 4.2 推理方式分类

#### 方式 A：派生属性（Derived Properties）

基于 Link 的简单聚合计算。例如：
- "计算一个机场关联的所有航线的总告警数"
- 在 Object View 中，派生属性实时计算并展示

#### 方式 B：Functions 中的 Link 遍历

```typescript
// 伪代码：Functions 中的 Link 遍历
function getSupplyChainImpact(plant: Plant): ImpactReport {
    // 沿 Link 遍历：Plant → WorkOrder → Supplier → ...
    const workOrders = plant.links.workOrders;
    const suppliers = workOrders.flatMap(wo => wo.links.supplier);
    // 执行分析逻辑
    return { criticalSuppliers: suppliers.filter(s => s.risk > 0.8) };
}
```

#### 方式 C：AIP Logic（LLM 驱动的推理）

Palantir AIP 平台支持将 LLM 嵌入 Functions：

```typescript
// LLM 驱动的推理
function analyzeWorkOrder(workOrder: WorkOrder): AnalysisResult {
    const context = {
        description: workOrder.description,
        relatedAssets: workOrder.links.assets.map(a => a.name),
        history: workOrder.links.previousOrders.map(o => o.status)
    };
    return llmCall("分析工单风险", context);
}
```

### 4.3 推理引擎：Ontology Engine

Palantir 的 Ontology Engine 是三部分架构之一：

```
Language（语言层）— 定义 Object、Link、Action、Function
    ↓
Engine（引擎层）— 执行读取、写入、推理
    ├── Read Architecture: SQL 查询、实时订阅、物化视图
    ├── Write Architecture: 原子事务、批量写入、流式 CDC
    └── Compute: Spark 分布式计算、Function 执行、LLM 调用
    ↓
Toolchain（工具链）— OSDK、Workshop、Slate、Vertex
```

**推理能力分布在 Engine 的计算层中**，而不是一个独立的推理引擎。推理的具体实现由 Function 决定——可以是简单的规则引擎，也可以是复杂的 ML/LLM 调用。

### 4.4 与我们的方案对比

| 维度 | Palantir | 我们的方案（计划） |
|------|----------|-------------------|
| **推理引擎** | 无独立推理引擎，推理由 Function 实现 | 计划中的 `engine/` 模块 |
| **Link 遍历** | 通过 OSDK 或 Function 编程实现 | 计划使用 SQLite 递归 CTE + Python BFS |
| **冲突检测** | Function 中手动编码 | 计划实现自动检测（`conflicts_with` 关系查询） |
| **影响分析** | Function 中手动编码 | 计划实现 BFS 影响链遍历 |
| **一致性检查** | 无自动化（依赖 Pipeline 验证） | 计划中 |
| **LLM 在推理中的角色** | AIP Logic 可选调用 | 核心推理引擎（抽取 + 分析） |

---

## 五、关键发现与对我们的启示

### 5.1 最重要的发现

1. **Palantir 的关系建立是确定性的，不是发现性的**
   - 关系来自结构化数据的外键映射，不是从非结构化文本中挖掘
   - 这意味着我们的 LLM 抽取方案面临的挑战比 Palantir 大得多

2. **Palantir 没有使用图数据库**
   - Links 通过索引层实现，本质上是"邻接表 + 分布式计算"
   - 验证了我们在 SQLite 中实现关系存储的可行性（SQLite 递归 CTE 可以做到类似的查询）

3. **Palantir 没有独立的推理引擎**
   - 推理能力分布在 Function 中，由开发者编程实现
   - 这意味着"前向推理"在 Palantir 中是**程序式的**，不是**声明式的**

4. **Palantir 的推理是写回式（Action → 写回源系统）**
   - 推理结果不只是展示，而是触发 Action 写回业务系统
   - 我们的方案目前只考虑了分析（读），没有考虑 Action（写）

### 5.2 对我们方案设计的启发

| 启发 | 具体建议 |
|------|---------|
| **关系类型应该更语义化** | Palantir 的 Link Type 名称是语义化的（`employer`, `assigned_aircraft`），我们的 8 种通用类型（depends_on, implements）可能不够丰富 |
| **考虑 Action 机制** | 推理结果不应只是展示，应考虑 MCP Tool 写回 PRD 或代码注释 |
| **Function 作为推理容器** | 我们计划中的 `engine/` 模块可以借鉴 Function 概念，每个推理功能是一个独立的 Function |
| **派生属性** | 简单的聚合计算（如"受影响的模块数量"）可以通过 SQL 查询实现，不需要复杂推理 |
| **检索 UX 参考 Vertex** | Vertex 的 Search Around 是多步关系遍历的好设计，可以借鉴到我们的 MCP Tool 中 |

### 5.3 Palantir 的完整工作流程总结

```
Pipeline (ETL) → Object Type + Link Type (Schema) → Object Storage (Data)
    ↓
Ontology Engine (Query + Compute)
    ├── Object Views (单 Object 查询)
    ├── Vertex (多 Object 图遍历)
    ├── OSDK (程序化查询)
    └── Functions (推理 + 计算)
        ↓
Actions (写回业务系统)
```