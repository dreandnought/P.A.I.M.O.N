"""
PRD Ontology MCP Server

为 Coding Agent (Claude Code, Cline 等) 提供 PRD 增强理解的 MCP 插件。
"""

from fastmcp import FastMCP

from tools.parse_prd import register as register_parse_prd
from tools.query_ontology import register as register_query_ontology
from tools.ingest_document import register as register_ingest_document
from tools.modify_ontology import register as register_modify_ontology
from tools.delete_entity import register as register_delete_entity
from tools.manage_schema import register as register_manage_schema

# 创建 MCP 服务器
mcp = FastMCP(
    "PRD Ontology MCP",
)

# 注册工具
register_parse_prd(mcp)
register_query_ontology(mcp)
register_ingest_document(mcp)
register_modify_ontology(mcp)
register_delete_entity(mcp)
register_manage_schema(mcp)


def main():
    """运行 MCP 服务器。"""
    mcp.run()


if __name__ == "__main__":
    main()
