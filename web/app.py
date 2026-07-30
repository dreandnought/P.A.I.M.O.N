"""
PRD Ontology MCP — Web Dashboard Backend

Flask backend serving ontology graph data and LLM API configuration.
Serves the Nothing-UI-styled frontend.
"""

import json
import os
import sys

import yaml
from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schema import get_connection, get_db_path, init_db
from models.entity import get_entity, get_entity_by_name, search_entities, list_all_entities
from models.relation import get_entity_relations
from engine.cache import get_cache
from engine.feedback import get_feedback_stats, list_recent_feedback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
UI_DESIGN_DIR = os.path.join(PROJECT_DIR, "vibe-nothing-ui-design")
LLM_CONFIG_PATH = os.path.join(PROJECT_DIR, "llm_config.yaml")

app = Flask(__name__, static_folder=None)


def _normalize_llm_url(url: str) -> str:
    """Normalize OpenAI-style chat completions URLs.

    Appends ``/chat/completions`` if it is not already present, preserving the
    base path as-is. This covers both standard endpoints (``/api/v3``,
    ``/v1``) and Ark plan endpoints (``/api/plan/v3``, ``/api/coding/v3``).
    """
    if not url:
        return url
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/")
    if path.endswith("/chat/completions"):
        return url
    return url.rstrip("/") + "/chat/completions"


# ── Static file serving ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/css/<path:filename>")
def serve_css(filename):
    return send_from_directory(os.path.join(UI_DESIGN_DIR, "css"), filename)


@app.route("/js/<path:filename>")
def serve_js(filename):
    return send_from_directory(os.path.join(UI_DESIGN_DIR, "js"), filename)


@app.route("/fonts/<path:filename>")
def serve_fonts(filename):
    return send_from_directory(os.path.join(UI_DESIGN_DIR, "fonts"), filename)


@app.route("/web/<path:filename>")
def serve_web_static(filename):
    return send_from_directory(BASE_DIR, filename)


# ── API endpoints ────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    """Database statistics."""
    conn = get_connection()
    entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    relation_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    type_dist = conn.execute(
        "SELECT et.name AS type_name, COUNT(*) AS cnt "
        "FROM entities e JOIN entity_types et ON e.type_id = et.id "
        "GROUP BY et.name ORDER BY cnt DESC"
    ).fetchall()
    rel_type_dist = conn.execute(
        "SELECT rt.name AS type_name, COUNT(*) AS cnt "
        "FROM relations r JOIN relation_types rt ON r.type_id = rt.id "
        "GROUP BY rt.name ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return jsonify({
        "entities": entity_count,
        "relations": relation_count,
        "entity_types": [dict(r) for r in type_dist],
        "relation_types": [dict(r) for r in rel_type_dist],
    })


@app.route("/api/graph")
def api_graph():
    """Full graph data: all nodes and edges in one payload."""
    conn = get_connection()

    nodes = conn.execute(
        "SELECT e.id, e.name, e.type_id, et.name AS type_name, "
        "e.description, e.status, e.confidence "
        "FROM entities e JOIN entity_types et ON e.type_id = et.id "
        "WHERE e.status = 'active' "
        "ORDER BY e.type_id, e.name"
    ).fetchall()

    edges = conn.execute(
        "SELECT r.id, r.type_id, rt.name AS type_name, "
        "r.source_id, r.target_id, r.confidence, r.weight, r.metadata "
        "FROM relations r JOIN relation_types rt ON r.type_id = rt.id "
        "ORDER BY r.confidence DESC"
    ).fetchall()

    conn.close()
    return jsonify({
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges],
    })


@app.route("/api/entities")
def api_entities():
    """List all entities, optionally filtered by type."""
    type_filter = request.args.get("type")
    conn = get_connection()
    if type_filter:
        rows = conn.execute(
            "SELECT e.*, et.name AS type_name FROM entities e "
            "JOIN entity_types et ON e.type_id = et.id "
            "WHERE et.name = ? ORDER BY e.name",
            (type_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT e.id, e.name, e.type_id, et.name AS type_name, "
            "e.description, e.status, e.confidence, e.source "
            "FROM entities e JOIN entity_types et ON e.type_id = et.id "
            "ORDER BY e.type_id, e.name"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/entity/<entity_id>")
def api_entity_detail(entity_id):
    """Entity detail with all relations."""
    entity = get_entity(entity_id)
    if not entity:
        return jsonify({"error": "Entity not found"}), 404
    relations = get_entity_relations(entity_id)
    return jsonify({"entity": entity, "relations": relations})


@app.route("/api/relations")
def api_relations():
    """List all relations."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT r.id, r.type_id, rt.name AS type_name, "
        "r.source_id, r.target_id, r.confidence, r.weight, r.metadata, "
        "se.name AS source_name, se.type_id AS source_type, "
        "te.name AS target_name, te.type_id AS target_type "
        "FROM relations r "
        "JOIN relation_types rt ON r.type_id = rt.id "
        "JOIN entities se ON r.source_id = se.id "
        "JOIN entities te ON r.target_id = te.id "
        "ORDER BY r.confidence DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/search")
def api_search():
    """Search entities by keyword."""
    q = request.args.get("q", "")
    if not q:
        return jsonify([])
    results = search_entities(q, limit=20)
    return jsonify(results)


def _load_llm_yaml():
    """Load the project-wide llm_config.yaml."""
    if not os.path.exists(LLM_CONFIG_PATH):
        return _default_llm_config()
    with open(LLM_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or _default_llm_config()


def _default_llm_config():
    return {
        "default_provider": "siliconflow",
        "providers": {
            "siliconflow": {
                "api_key": "",
                "base_url": "https://api.siliconflow.cn/v1",
                "models": {"chat": "deepseek-ai/DeepSeek-V4-Flash"},
                "description": "SiliconFlow 硅基流动",
            }
        },
        "prd_parser": {
            "provider": "siliconflow",
            "model": "deepseek-ai/DeepSeek-V4-Flash",
            "temperature": 0.1,
            "max_retries": 3,
            "extraction": {
                "mode": "two_phase",
                "entity_confidence_threshold": 0.5,
                "relation_confidence_threshold": 0.6,
            },
        },
        "prd_generator": {
            "provider": "siliconflow",
            "model": "deepseek-ai/DeepSeek-V4-Flash",
            "temperature": 0.7,
            "max_tokens": 8192,
            "system_prompt_file": "prd_samples/prd-writer/PROMPT.md",
        },
    }


def _save_llm_yaml(cfg):
    """Write the project-wide llm_config.yaml preserving comments is not possible; rely on structure."""
    with open(LLM_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, indent=2)


def _provider_chat_url(provider_cfg):
    """Build a chat completions URL from a provider config."""
    base = _normalize_llm_url(provider_cfg.get("base_url", "")).rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


@app.route("/api/llm-config", methods=["GET"])
def api_get_llm_config():
    """Get current LLM API configuration from llm_config.yaml."""
    cfg = _load_llm_yaml()
    # Normalize provider URLs for the frontend
    for name, p in cfg.get("providers", {}).items():
        p["base_url"] = _normalize_llm_url(p.get("base_url", ""))
    return jsonify(cfg)


@app.route("/api/llm-config", methods=["POST"])
def api_save_llm_config():
    """Save LLM API configuration back to llm_config.yaml."""
    data = request.get_json() or {}
    cfg = _load_llm_yaml()

    # Update default provider
    if "default_provider" in data:
        cfg["default_provider"] = data["default_provider"]

    # Update providers
    providers = data.get("providers", {})
    if providers:
        cfg["providers"] = providers
        for name, p in cfg["providers"].items():
            p["base_url"] = _normalize_llm_url(p.get("base_url", ""))

    # Update prd_parser / prd_generator sections
    for section in ("prd_parser", "prd_generator"):
        if section in data:
            cfg[section] = {**cfg.get(section, {}), **data[section]}

    _save_llm_yaml(cfg)
    return jsonify({"status": "saved", "config": cfg})


@app.route("/api/llm-config/test", methods=["POST"])
def api_test_llm_config():
    """Test LLM API connection with provided config or a named provider."""
    data = request.get_json() or {}

    # If provider name given, load its config from YAML
    provider_name = data.get("provider")
    if provider_name:
        cfg = _load_llm_yaml()
        provider = cfg.get("providers", {}).get(provider_name, {})
        api_url = _provider_chat_url(provider)
        api_key = provider.get("api_key", "")
        model = data.get("model") or provider.get("models", {}).get("chat", "")
    else:
        api_url = _normalize_llm_url(data.get("api_url", ""))
        api_key = data.get("api_key", "")
        model = data.get("model", "")

    if not api_url or not api_key or not model:
        return jsonify({"ok": False, "error": "Missing api_url/base_url, api_key, or model"}), 400

    try:
        import requests as req
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "ping"},
            ],
            "max_tokens": 5,
            "temperature": 0,
        }
        resp = req.post(api_url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        return jsonify({"ok": True, "message": "Connection successful"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


# ── Phase 4: Reasoning & Feedback APIs ──────────────────────────────

@app.route("/api/reasoning/run", methods=["POST"])
def api_reasoning_run():
    """Run reasoning engine on specified entities."""
    data = request.get_json() or {}
    entity_ids = data.get("entity_ids", [])
    entity_names = data.get("entity_names", [])
    query = data.get("query")
    rules_only = data.get("rules_only")

    if not entity_ids and not entity_names and not query:
        return jsonify({"error": "Provide entity_ids, entity_names, or query"}), 400

    from tools.reason_ontology import _resolve_entity_ids, _build_engine

    resolved_ids = _resolve_entity_ids(entity_ids, entity_names, query)
    if not resolved_ids:
        return jsonify({"error": "No matching entities found"}), 404

    engine = _build_engine(None, rules_only)
    output = engine.run(resolved_ids)

    return jsonify({
        "entity_ids": resolved_ids,
        "inferences": [r.to_dict() for r in output.inferences],
        "conflicts": [r.to_dict() for r in output.conflicts],
        "stats": output.stats,
        "llm_summary": output.to_llm_format(),
    })


@app.route("/api/cache/stats")
def api_cache_stats():
    """Cache statistics."""
    return jsonify(get_cache().stats())


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Clear all cache."""
    get_cache().invalidate_all()
    return jsonify({"status": "cleared"})


@app.route("/api/feedback/stats")
def api_feedback_stats():
    """Feedback statistics."""
    return jsonify(get_feedback_stats())


@app.route("/api/feedback/list")
def api_feedback_list():
    """List recent feedback."""
    limit = int(request.args.get("limit", 20))
    return jsonify({"feedback": list_recent_feedback(limit)})


@app.route("/api/feedback/submit", methods=["POST"])
def api_feedback_submit():
    """Submit feedback for a prediction."""
    from engine.feedback import submit_feedback

    data = request.get_json() or {}
    prediction_id = data.get("prediction_id")
    status = data.get("status")
    actual_result = data.get("actual_result")
    developer_note = data.get("developer_note")
    pr_id = data.get("pr_id")

    if not prediction_id or not status:
        return jsonify({"error": "prediction_id and status are required"}), 400

    result = submit_feedback(
        prediction_id=prediction_id,
        status=status,
        actual_result=actual_result,
        developer_note=developer_note,
        pr_id=pr_id,
    )
    return jsonify(result)


if __name__ == "__main__":
    # Ensure DB is initialised
    if not os.path.exists(get_db_path()):
        init_db()
    port = int(os.environ.get("PORT", 5258))
    print(f"PRD Ontology Dashboard → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
