"""
MCP Tool: ingest_document

从文本或 Markdown 文档中抽取实体和关系，并写入 Ontology 数据库。
默认 dry_run=true，先返回变更计划；dry_run=false 时执行写入。
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from parser.llm_parser import extract_entities_and_relations
from models.entity import (
    entity_exists,
    find_or_create_entity,
    get_entity,
    resolve_entity_by_name_or_id,
    update_entity,
)
from models.relation import create_relation
from models.document import create_document


class EntityChangeItem(BaseModel):
    """实体变更项（创建或更新）。"""

    entity_id: str = Field(..., description="实体 ID")
    name: str = Field(..., description="实体名称")
    type: str = Field(..., description="实体类型 ID")
    description: str = Field(default="", description="实体描述")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="置信度")


class RelationChangeItem(BaseModel):
    """关系变更项（创建）。"""

    source_id: str = Field(..., description="源实体 ID")
    target_id: str = Field(..., description="目标实体 ID")
    relation_type: str = Field(..., description="关系类型 ID")
    description: str = Field(default="", description="关系描述")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="置信度")


class SkippedRelationItem(BaseModel):
    """被跳过的关系项。"""

    source_name: str = Field(default="", description="源实体名称")
    target_name: str = Field(default="", description="目标实体名称")
    relation_type: str = Field(default="", description="关系类型")
    reason: str = Field(default="", description="跳过原因")


class EntityChangeGroup(BaseModel):
    """实体变更分组。"""

    create: List[EntityChangeItem] = Field(default_factory=list)
    update: List[EntityChangeItem] = Field(default_factory=list)
    delete: List[EntityChangeItem] = Field(default_factory=list)


class RelationChangeGroup(BaseModel):
    """关系变更分组。"""

    create: List[RelationChangeItem] = Field(default_factory=list)
    update: List[RelationChangeItem] = Field(default_factory=list)
    delete: List[RelationChangeItem] = Field(default_factory=list)


class IngestChangePlan(BaseModel):
    """ingest_document 工具执行写入时接收的变更计划结构。"""

    entities: EntityChangeGroup = Field(default_factory=EntityChangeGroup)
    relations: RelationChangeGroup = Field(default_factory=RelationChangeGroup)
    skipped_relations: List[SkippedRelationItem] = Field(default_factory=list)

    @field_validator("entities", "relations", mode="before")
    @classmethod
    def _coerce_group(cls, value):
        """允许前端传 None 时自动转为空分组，避免后续遍历报错。"""
        if value is None:
            return {}
        return value

    @field_validator("skipped_relations", mode="before")
    @classmethod
    def _coerce_skipped(cls, value):
        """允许前端传 None 时自动转为空列表。"""
        return value if value is not None else []


def _allocate_entity_id(name, suggested_id, existing_ids, db_path):
    """为 dry-run 阶段分配一个稳定的实体 ID（不写入数据库）。"""
    if suggested_id and suggested_id not in existing_ids and entity_exists(suggested_id, db_path):
        return suggested_id
    existing = resolve_entity_by_name_or_id(name, db_path)
    if existing:
        return existing["id"]
    if suggested_id and suggested_id not in existing_ids:
        return suggested_id
    # 若 suggested_id 冲突或为空，生成 fallback ID
    import re
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_").lower()[:32] or "entity"
    return f"new:{slug}"


def _build_change_plan(extraction, db_path):
    """根据 LLM 抽取结果构建变更计划（不写入数据库）。"""
    name_to_id = {}
    existing_ids = set()

    entities_create = []
    entities_update = []

    for e in extraction["entities"]:
        matched_id = e.get("matched_entity_id")
        if matched_id and entity_exists(matched_id, db_path):
            entities_update.append({
                "entity_id": matched_id,
                "name": e["name"],
                "type": e["type"],
                "description": e["description"],
                "confidence": e["confidence"],
            })
            name_to_id[e["name"]] = matched_id
            existing_ids.add(matched_id)
            continue

        # 尝试按名称查找
        existing = resolve_entity_by_name_or_id(e["name"], db_path)
        if existing:
            entities_update.append({
                "entity_id": existing["id"],
                "name": e["name"],
                "type": e["type"],
                "description": e["description"],
                "confidence": e["confidence"],
            })
            name_to_id[e["name"]] = existing["id"]
            existing_ids.add(existing["id"])
            continue

        allocated_id = _allocate_entity_id(e["name"], e.get("suggested_id"), existing_ids, db_path)
        # 处理同一次摄入内的 ID 冲突
        base_id = allocated_id
        counter = 2
        while allocated_id in existing_ids:
            allocated_id = f"{base_id}_{counter}"
            counter += 1

        entities_create.append({
            "entity_id": allocated_id,
            "name": e["name"],
            "type": e["type"],
            "description": e["description"],
            "confidence": e["confidence"],
        })
        name_to_id[e["name"]] = allocated_id
        existing_ids.add(allocated_id)

    relations_create = []
    skipped_relations = []

    for r in extraction["relations"]:
        source_id = r.get("source_id") or name_to_id.get(r["source_name"])
        target_id = r.get("target_id") or name_to_id.get(r["target_name"])

        if not source_id:
            source = resolve_entity_by_name_or_id(r["source_name"], db_path)
            source_id = source["id"] if source else None
        if not target_id:
            target = resolve_entity_by_name_or_id(r["target_name"], db_path)
            target_id = target["id"] if target else None

        if source_id and target_id:
            relations_create.append({
                "source_id": source_id,
                "relation_type": r["relation_type"],
                "target_id": target_id,
                "description": r["description"],
                "confidence": r["confidence"],
            })
        else:
            skipped_relations.append({
                "source_name": r["source_name"],
                "relation_type": r["relation_type"],
                "target_name": r["target_name"],
                "reason": "无法解析源或目标实体",
            })

    return {
        "entities": {"create": entities_create, "update": entities_update, "delete": []},
        "relations": {"create": relations_create, "update": [], "delete": []},
        "skipped_relations": skipped_relations,
    }


def _execute_plan(plan, title, content, db_path):
    """执行变更计划，写入数据库，并创建文档记录。"""
    doc = create_document(title=title, content=content, doc_type="ingest", db_path=db_path)
    doc_id = doc["id"]

    created_entities = []
    updated_entities = []
    created_relations = []

    name_to_id = {}

    for e in plan["entities"]["create"]:
        entity, created = find_or_create_entity(
            name=e["name"],
            type_id=e["type"],
            description=e["description"],
            suggested_id=e["entity_id"],
            confidence=e.get("confidence", 0.8),
            source="llm",
            source_doc_id=doc_id,
            db_path=db_path,
        )
        name_to_id[e["name"]] = entity["id"]
        if created:
            created_entities.append(entity)

    for e in plan["entities"]["update"]:
        entity_id = e["entity_id"]
        entity = get_entity(entity_id, db_path)
        if entity:
            # 合并描述：保留原描述，追加新描述
            original_desc = (entity.get("description") or "").strip()
            new_desc = e.get("description", "").strip()
            if new_desc and new_desc not in original_desc:
                merged_desc = f"{original_desc}\n{new_desc}".strip()
            else:
                merged_desc = original_desc or new_desc
            update_entity(
                entity_id,
                {
                    "description": merged_desc,
                    "confidence": max(entity.get("confidence", 0.8), e.get("confidence", 0.8)),
                },
                db_path=db_path,
            )
            updated_entities.append(get_entity(entity_id, db_path))
            name_to_id[e["name"]] = entity_id

    for r in plan["relations"]["create"]:
        source_id = r["source_id"]
        target_id = r["target_id"]
        relation = create_relation(
            type_id=r["relation_type"],
            source_id=source_id,
            target_id=target_id,
            confidence=r.get("confidence", 0.8),
            source="llm",
            source_doc_id=doc_id,
            metadata={"description": r.get("description", "")},
            db_path=db_path,
            valid_from=r.get("valid_from"),
            valid_until=r.get("valid_until"),
        )
        created_relations.append(relation)

    return {
        "document": doc,
        "created_entities": created_entities,
        "updated_entities": updated_entities,
        "created_relations": created_relations,
        "skipped_relations": plan.get("skipped_relations", []),
    }


def register(mcp):
    """注册 ingest_document 工具到 MCP 服务器。"""

    @mcp.tool()
    def ingest_document(
        content: str = "",
        title: Optional[str] = "未命名文档",
        dry_run: bool = True,
        plan: Optional[dict] = None,
        db_path: Optional[str] = None,
    ) -> dict:
        """从文本或 Markdown 文档抽取实体和关系，并写入 Ontology 数据库。

        Args:
            content: 文本或 Markdown 文档内容
            title: 文档标题（用于溯源）
            dry_run: 为 True 时只返回变更计划，不写入数据库；为 False 时执行写入
            plan: 可选，传入上一次 dry_run 返回的 plan，dry_run=false 时直接执行该 plan，避免 LLM 两次调用结果不一致
            db_path: 可选，Ontology SQLite 数据库路径

        Returns:
            dry_run=true: {"dry_run": true, "plan": {...}, "extraction_summary": {...}}
            dry_run=false: {"dry_run": false, "document": {...}, "created_entities": [...], "updated_entities": [...], "created_relations": [...], "skipped_relations": [...]}
        """
        if plan is None:
            extraction = extract_entities_and_relations(content, db_path)
            plan = _build_change_plan(extraction, db_path)
            extraction_summary = {
                "entities_extracted": len(extraction["entities"]),
                "relations_extracted": len(extraction["relations"]),
            }
        else:
            # 校验 plan 结构，防止传入畸形 plan 导致执行阶段崩溃
            try:
                validated_plan = IngestChangePlan.model_validate(plan)
            except Exception as validation_error:
                return {
                    "dry_run": False,
                    "success": False,
                    "error": f"plan 结构校验失败: {validation_error}",
                    "error_type": "plan_validation_error",
                }
            # 校验通过后转回 dict，保持 _execute_plan 的原有接口不变
            plan = validated_plan.model_dump()
            extraction_summary = {"entities_extracted": None, "relations_extracted": None}

        if dry_run:
            return {
                "dry_run": True,
                "plan": plan,
                "extraction_summary": extraction_summary,
            }

        result = _execute_plan(plan, title, content, db_path)
        result["dry_run"] = False
        result["success"] = True
        return result

    return ingest_document
