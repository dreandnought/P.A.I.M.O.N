"""
SQLite 表定义和初始化。
遵循 ONTOLOGY_BUILD_GUIDE.md 中定义的数据模型协议。
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ontology.db")

SCHEMA_SQL = """
-- 实体类型表
CREATE TABLE IF NOT EXISTS entity_types (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    parent_id   TEXT REFERENCES entity_types(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 关系类型表
CREATE TABLE IF NOT EXISTS relation_types (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    symmetric   INTEGER NOT NULL DEFAULT 0,
    transitive  INTEGER NOT NULL DEFAULT 0,
    inverse_of  TEXT REFERENCES relation_types(id),
    domain_type TEXT,
    range_type  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 实体实例表（核心）
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    type_id         TEXT NOT NULL REFERENCES entity_types(id),
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'deprecated', 'removed')),
    confidence      REAL NOT NULL DEFAULT 0.8,
    source          TEXT NOT NULL DEFAULT 'manual',
    source_doc_id   TEXT,
    source_ref      TEXT,
    properties      TEXT DEFAULT '{}',
    tags            TEXT DEFAULT '[]',
    available_from  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type_id);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status);
CREATE INDEX IF NOT EXISTS idx_entities_available_from ON entities(available_from);

-- 关系实例表（核心）
CREATE TABLE IF NOT EXISTS relations (
    id              TEXT PRIMARY KEY,
    type_id         TEXT NOT NULL REFERENCES relation_types(id),
    source_id       TEXT NOT NULL REFERENCES entities(id),
    target_id       TEXT NOT NULL REFERENCES entities(id),
    weight          REAL NOT NULL DEFAULT 1.0,
    confidence      REAL NOT NULL DEFAULT 0.8,
    source          TEXT NOT NULL DEFAULT 'manual',
    source_doc_id   TEXT,
    metadata        TEXT DEFAULT '{}',
    valid_from      TEXT,
    valid_until     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(type_id, source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(type_id);
CREATE INDEX IF NOT EXISTS idx_relations_st ON relations(source_id, type_id);
CREATE INDEX IF NOT EXISTS idx_relations_ts ON relations(target_id, type_id);
CREATE INDEX IF NOT EXISTS idx_relations_valid_from ON relations(valid_from);
CREATE INDEX IF NOT EXISTS idx_relations_valid_until ON relations(valid_until);

-- 来源文档表
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    doc_type        TEXT NOT NULL DEFAULT 'prd',
    url             TEXT,
    file_path       TEXT,
    content_hash    TEXT,
    word_count      INTEGER,
    status          TEXT NOT NULL DEFAULT 'parsed'
                    CHECK(status IN ('pending', 'parsing', 'parsed', 'failed')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    parsed_at       TEXT
);
"""

# 预置数据
DEFAULT_ENTITY_TYPES = [
    ("requirement", "需求", "业务需求或用户需求", None),
    ("function", "功能", "系统功能点", "requirement"),  # parent: requirement
    ("module", "模块", "代码模块或子系统", None),
    ("interface", "接口", "API 接口或服务接口", "module"),  # parent: module
    ("data_entity", "数据实体", "数据库表或数据模型", "actor"),  # parent: actor
    ("test_case", "测试用例", "测试用例或测试场景", "requirement"),  # parent: requirement
    ("constraint", "约束", "业务约束或技术约束", "requirement"),  # parent: requirement
    ("actor", "角色", "用户角色或系统角色", None),
]

# (id, name, description, symmetric, transitive, inverse_of, domain_type, range_type)
DEFAULT_RELATION_TYPES = [
    ("depends_on", "依赖", "A 依赖 B 才能正常工作", 0, 1, None, None, None),
    ("causes", "因果", "A 的发生会导致 B", 0, 1, None, None, None),  # transitive=1
    ("constrains", "约束", "A 对 B 有约束条件", 0, 0, None, "constraint", None),
    ("impacts", "影响", "修改 A 会影响 B", 0, 0, None, None, None),
    ("conflicts_with", "冲突", "A 和 B 互斥或冲突", 1, 0, None, None, None),
    ("derived_from", "派生", "A 是从 B 派生/衍生出来的", 0, 1, None, None, None),
    ("implements", "实现", "A 实现了 B（接口/需求）", 0, 0, None, "function,module", "interface,requirement"),
    ("contains", "包含", "A 包含 B（父子关系）", 0, 1, None, "module,requirement", None),
    ("refines", "细化", "A 是对 B 的细化/补充", 0, 0, None, "function", "requirement,function"),
    ("relates_to", "关联", "A 和 B 有关联（通用关系）", 1, 0, None, None, None),
]


def get_db_path():
    """获取数据库文件路径。可通过环境变量覆盖。"""
    return os.environ.get("ONTOLOGY_DB_PATH", DB_PATH)


def init_db(db_path=None):
    """初始化数据库：创建表并插入预置数据。"""
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    # 插入预置实体类型
    for row in DEFAULT_ENTITY_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO entity_types (id, name, description, parent_id) VALUES (?, ?, ?, ?)",
            row,
        )

    # 插入预置关系类型
    for row in DEFAULT_RELATION_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO relation_types (id, name, description, symmetric, transitive, inverse_of, domain_type, range_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    conn.commit()
    conn.close()
    return path


def get_connection(db_path=None):
    """获取数据库连接。"""
    path = db_path or get_db_path()
    if not os.path.exists(path):
        init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 自动迁移：确保 relations 表包含时间字段（幂等）
    _migrate_relation_time_fields(conn)
    # 自动迁移：确保 entities 表包含 available_from 字段（幂等）
    _migrate_entity_time_fields(conn)
    # 自动迁移：确保 relation_types 表包含 inverse_of/domain_type/range_type 字段（幂等）
    _migrate_relation_type_fields(conn)
    return conn


def _migrate_relation_time_fields(conn):
    """自动迁移 relations 表，补充 valid_from / valid_until 字段（幂等）。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(relations)").fetchall()}
        changed = False
        if "valid_from" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_from TEXT")
            changed = True
        if "valid_until" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_until TEXT")
            changed = True
        if changed:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_valid_from ON relations(valid_from)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_valid_until ON relations(valid_until)")
            conn.commit()
    except Exception:
        # 迁移失败不应阻塞连接（例如表结构异常时静默降级）
        pass


def _migrate_entity_time_fields(conn):
    """自动迁移 entities 表，补充 available_from 字段（幂等）。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()}
        if "available_from" not in cols:
            conn.execute("ALTER TABLE entities ADD COLUMN available_from TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_available_from ON entities(available_from)")
            conn.commit()
    except Exception:
        pass


def _migrate_relation_type_fields(conn):
    """自动迁移 relation_types 表，补充 inverse_of/domain_type/range_type 字段（幂等）。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(relation_types)").fetchall()}
        changed = False
        if "inverse_of" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN inverse_of TEXT REFERENCES relation_types(id)")
            changed = True
        if "domain_type" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN domain_type TEXT")
            changed = True
        if "range_type" not in cols:
            conn.execute("ALTER TABLE relation_types ADD COLUMN range_type TEXT")
            changed = True
        if changed:
            conn.commit()
    except Exception:
        pass
