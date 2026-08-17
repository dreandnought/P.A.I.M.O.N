/**
 * Force-directed graph visualization for PRD Ontology.
 * Pure vanilla JS + SVG — no dependencies.
 */

function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const TYPE_COLORS = {
  module: '#EDEDED',
  function: '#CFCFCF',
  interface: '#8C8C8C',
  data_entity: '#5A5A5A',
  constraint: '#FF4438',
  requirement: '#7BE38A',
  test_case: '#F2C94C',
  actor: '#3A3A3A',
};

const TYPE_RADIUS = {
  module: 8,
  function: 6,
  interface: 5,
  data_entity: 4,
  constraint: 5,
  requirement: 6,
  test_case: 4,
  actor: 5,
};

// ── 时间判定（实/虚关系、当前/未来实体）─────────────────────────
// 虚关系：valid_from 非空且晚于 now（尚未生效）
function isFutureRelation(edge, now) {
  if (!edge || !edge.valid_from) return false;
  if (!now) return false;
  return String(edge.valid_from) > String(now);
}

// 未来实体：available_from 非空且晚于 now
function isFutureEntity(node, now) {
  if (!node || !node.available_from) return false;
  if (!now) return false;
  return String(node.available_from) > String(now);
}

class ForceGraph {
  constructor(svg, width, height) {
    this.svg = svg;
    this.width = width;
    this.height = height;
    this.nodes = [];
    this.edges = [];
    this.nodeMap = new Map();
    this.simulationRunning = true;
    this.alpha = 1.0;
    this.alphaDecay = 0.005;
    this.alphaMin = 0.02;
    this.velocityDecay = 0.6;
    // 当前时间（ISO 8601 UTC），由 setData 通过 payload.now 提供
    this.now = null;

    // Physics params
    this.repulsionStrength = 1200;
    this.springLength = 80;
    this.springStrength = 0.04;
    this.centeringStrength = 0.005;

    // View transform
    this.viewX = 0;
    this.viewY = 0;
    this.viewScale = 1;

    // Interaction state
    this.draggedNode = null;
    this.dragOffset = { x: 0, y: 0 };
    this.isPanning = false;
    this.panStart = { x: 0, y: 0 };

    // SVG layers
    this.svg.innerHTML = `
      <defs>
        <marker id="arrow" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#3A3A3A" />
        </marker>
      </defs>
      <g class="view-group">
        <g class="edges-layer"></g>
        <g class="nodes-layer"></g>
        <g class="labels-layer"></g>
      </g>
    `;
    this.viewGroup = this.svg.querySelector('.view-group');
    this.edgesLayer = this.svg.querySelector('.edges-layer');
    this.nodesLayer = this.svg.querySelector('.nodes-layer');
    this.labelsLayer = this.svg.querySelector('.labels-layer');

    this.selectedNode = null;
    this.onSelectCallback = null;

    this._bindEvents();
  }

  setData(nodes, edges, now) {
    this.now = now || null;
    this.nodes = nodes.map((n, i) => ({
      ...n,
      x: this.width / 2 + (Math.random() - 0.5) * 300,
      y: this.height / 2 + (Math.random() - 0.5) * 300,
      vx: 0,
      vy: 0,
      index: i,
    }));
    this.nodeMap = new Map(this.nodes.map(n => [n.id, n]));
    this.edges = edges.filter(e => this.nodeMap.has(e.source_id) && this.nodeMap.has(e.target_id));
    this.alpha = 1.0;
    this._render();
    this._tick();
  }

  onSelect(fn) {
    this.onSelectCallback = fn;
  }

  filterNodes(visibleTypes) {
    this.nodes.forEach(n => {
      n.visible = !visibleTypes || visibleTypes.size === 0 || visibleTypes.has(n.type_id);
    });
    this._render();
  }

  // ── Physics simulation ──────────────────────────────────────────

  _tick() {
    if (!this.simulationRunning) return;

    if (this.alpha > this.alphaMin) {
      this.alpha -= this.alphaDecay;
      this._simulate();
      this._updatePositions();
      requestAnimationFrame(() => this._tick());
    } else {
      this.simulationRunning = false;
      this._updatePositions();
    }
  }

  _simulate() {
    const nodes = this.nodes;
    const n = nodes.length;
    const cx = this.width / 2;
    const cy = this.height / 2;

    // Repulsion (O(n²) — fine for <500 nodes)
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 1) dist2 = 1;
        const dist = Math.sqrt(dist2);
        const force = this.repulsionStrength / dist2;
        const fx = (force * dx) / dist;
        const fy = (force * dy) / dist;
        a.vx -= fx * this.alpha;
        a.vy -= fy * this.alpha;
        b.vx += fx * this.alpha;
        b.vy += fy * this.alpha;
      }
    }

    // Spring attraction along edges
    for (const e of this.edges) {
      const a = this.nodeMap.get(e.source_id);
      const b = this.nodeMap.get(e.target_id);
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const diff = dist - this.springLength;
      const force = diff * this.springStrength;
      const fx = (force * dx) / dist;
      const fy = (force * dy) / dist;
      a.vx += fx * this.alpha;
      a.vy += fy * this.alpha;
      b.vx -= fx * this.alpha;
      b.vy -= fy * this.alpha;
    }

    // Centering + damping + integrate
    for (const node of nodes) {
      node.vx += (cx - node.x) * this.centeringStrength * this.alpha;
      node.vy += (cy - node.y) * this.centeringStrength * this.alpha;
      node.vx *= this.velocityDecay;
      node.vy *= this.velocityDecay;
      if (node !== this.draggedNode) {
        node.x += node.vx;
        node.y += node.vy;
      }
    }
  }

  // ── Rendering ───────────────────────────────────────────────────

  _render() {
    this._renderEdges();
    this._renderNodes();
    this._renderLabels();
  }

  _renderEdges() {
    this.edgesLayer.innerHTML = '';
    const now = this.now;
    for (const e of this.edges) {
      const a = this.nodeMap.get(e.source_id);
      const b = this.nodeMap.get(e.target_id);
      if (!a || !b) continue;
      if (a.visible === false || b.visible === false) continue;

      const isVirtual = isFutureRelation(e, now);
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', a.x);
      line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x);
      line.setAttribute('y2', b.y);
      if (isVirtual) {
        // 虚关系：虚线 + 强调色（accent-text）
        line.setAttribute('stroke', getCssVar('--accent-text') || '#FF4438');
        line.setAttribute('stroke-width', '1.2');
        line.setAttribute('stroke-dasharray', '4 3');
        line.setAttribute('opacity', '0.6');
      } else {
        line.setAttribute('stroke', getCssVar('--line'));
        line.setAttribute('stroke-width', '1');
        line.setAttribute('opacity', String(0.3 + e.confidence * 0.4));
      }
      line.dataset.edgeId = e.id;
      line.dataset.virtual = isVirtual ? '1' : '0';
      this.edgesLayer.appendChild(line);
    }
  }

  _renderNodes() {
    this.nodesLayer.innerHTML = '';
    const now = this.now;
    for (const node of this.nodes) {
      if (node.visible === false) continue;
      const r = TYPE_RADIUS[node.type_id] || 5;
      const color = TYPE_COLORS[node.type_id] || '#8C8C8C';
      const isFuture = isFutureEntity(node, now);

      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.classList.add('node-group');
      if (isFuture) g.classList.add('future-entity');
      g.dataset.nodeId = node.id;
      g.setAttribute('transform', `translate(${node.x},${node.y})`);
      g.style.cursor = 'pointer';
      if (isFuture) g.style.opacity = '0.55';

      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('r', r);
      circle.setAttribute('fill', color);
      if (isFuture) {
        // 未来实体：虚线边框（accent 色）
        circle.setAttribute('stroke', getCssVar('--accent-text') || '#FF4438');
        circle.setAttribute('stroke-width', '1.2');
        circle.setAttribute('stroke-dasharray', '2 2');
      } else if (node === this.selectedNode) {
        circle.setAttribute('stroke', getCssVar('--display'));
        circle.setAttribute('stroke-width', '2');
      } else {
        circle.setAttribute('stroke', getCssVar('--bg'));
        circle.setAttribute('stroke-width', '0');
      }
      g.appendChild(circle);

      g.addEventListener('mousedown', (ev) => this._onNodeMouseDown(ev, node));
      g.addEventListener('click', (ev) => this._onNodeClick(ev, node));

      this.nodesLayer.appendChild(g);
    }
  }

  _renderLabels() {
    this.labelsLayer.innerHTML = '';
    for (const node of this.nodes) {
      if (node.visible === false) continue;
      const r = TYPE_RADIUS[node.type_id] || 5;

      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('x', node.x);
      text.setAttribute('y', node.y + r + 12);
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('fill', '#5A5A5A');
      text.setAttribute('font-family', 'Geist Mono, monospace');
      text.setAttribute('font-size', '9');
      text.textContent = node.name.length > 20 ? node.name.slice(0, 18) + '…' : node.name;
      text.style.pointerEvents = 'none';
      this.labelsLayer.appendChild(text);
    }
  }

  _updatePositions() {
    // Update node positions
    const nodeGroups = this.nodesLayer.querySelectorAll('.node-group');
    nodeGroups.forEach(g => {
      const node = this.nodeMap.get(g.dataset.nodeId);
      if (node) {
        g.setAttribute('transform', `translate(${node.x},${node.y})`);
        if (node === this.selectedNode) {
          const circle = g.querySelector('circle');
          if (circle) {
            circle.setAttribute('stroke', getCssVar('--display'));
            circle.setAttribute('stroke-width', '2');
          }
        }
      }
    });

    // Update edge positions
    const lines = this.edgesLayer.querySelectorAll('line');
    lines.forEach(line => {
      const a = this.nodeMap.get(line.dataset.edgeId?.replace(/__.*$/, '') || '');
      // Find edge by scanning
      const edge = this.edges.find(e => e.id === line.dataset.edgeId);
      if (edge) {
        const src = this.nodeMap.get(edge.source_id);
        const tgt = this.nodeMap.get(edge.target_id);
        if (src && tgt) {
          line.setAttribute('x1', src.x);
          line.setAttribute('y1', src.y);
          line.setAttribute('x2', tgt.x);
          line.setAttribute('y2', tgt.y);
        }
      }
    });

    // Update label positions
    const texts = this.labelsLayer.querySelectorAll('text');
    let labelIdx = 0;
    for (const node of this.nodes) {
      if (node.visible === false) continue;
      const text = texts[labelIdx];
      if (text) {
        const r = TYPE_RADIUS[node.type_id] || 5;
        text.setAttribute('x', node.x);
        text.setAttribute('y', node.y + r + 12);
      }
      labelIdx++;
    }
  }

  // ── Interaction ─────────────────────────────────────────────────

  _bindEvents() {
    this.svg.addEventListener('mousedown', (e) => this._onSvgMouseDown(e));
    this.svg.addEventListener('mousemove', (e) => this._onSvgMouseMove(e));
    this.svg.addEventListener('mouseup', (e) => this._onSvgMouseUp(e));
    this.svg.addEventListener('mouseleave', (e) => this._onSvgMouseUp(e));
    this.svg.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });
  }

  _screenToWorld(sx, sy) {
    const rect = this.svg.getBoundingClientRect();
    const x = (sx - rect.left - this.viewX) / this.viewScale;
    const y = (sy - rect.top - this.viewY) / this.viewScale;
    return { x, y };
  }

  _onNodeMouseDown(ev, node) {
    ev.stopPropagation();
    this.draggedNode = node;
    const pos = this._screenToWorld(ev.clientX, ev.clientY);
    this.dragOffset = { x: pos.x - node.x, y: pos.y - node.y };
    this.alpha = Math.max(this.alpha, 0.5);
    if (!this.simulationRunning) {
      this.simulationRunning = true;
      this._tick();
    }
  }

  _onNodeClick(ev, node) {
    ev.stopPropagation();
    this.selectedNode = node;
    this._renderNodes();
    if (this.onSelectCallback) {
      this.onSelectCallback(node);
    }
  }

  _onSvgMouseDown(ev) {
    if (ev.target === this.svg || ev.target.tagName === 'svg') {
      this.isPanning = true;
      this.panStart = { x: ev.clientX - this.viewX, y: ev.clientY - this.viewY };
      // Deselect
      this.selectedNode = null;
      this._renderNodes();
      if (this.onSelectCallback) {
        this.onSelectCallback(null);
      }
    }
  }

  _onSvgMouseMove(ev) {
    if (this.draggedNode) {
      const pos = this._screenToWorld(ev.clientX, ev.clientY);
      this.draggedNode.x = pos.x - this.dragOffset.x;
      this.draggedNode.y = pos.y - this.dragOffset.y;
      this.draggedNode.vx = 0;
      this.draggedNode.vy = 0;
      this._updatePositions();
    } else if (this.isPanning) {
      this.viewX = ev.clientX - this.panStart.x;
      this.viewY = ev.clientY - this.panStart.y;
      this._applyViewTransform();
    }
  }

  _onSvgMouseUp(ev) {
    this.draggedNode = null;
    this.isPanning = false;
  }

  _onWheel(ev) {
    ev.preventDefault();
    const rect = this.svg.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const delta = ev.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.2, Math.min(5, this.viewScale * delta));
    // Zoom toward mouse position
    this.viewX = mx - (mx - this.viewX) * (newScale / this.viewScale);
    this.viewY = my - (my - this.viewY) * (newScale / this.viewScale);
    this.viewScale = newScale;
    this._applyViewTransform();
  }

  _applyViewTransform() {
    this.viewGroup.setAttribute('transform',
      `translate(${this.viewX},${this.viewY}) scale(${this.viewScale})`);
  }

  resetView() {
    this.viewX = 0;
    this.viewY = 0;
    this.viewScale = 1;
    this._applyViewTransform();
    this.alpha = 1.0;
    if (!this.simulationRunning) {
      this.simulationRunning = true;
      this._tick();
    }
  }

  resize(width, height) {
    this.width = width;
    this.height = height;
  }
}

// Export for use in index.html
window.ForceGraph = ForceGraph;
window.TYPE_COLORS = TYPE_COLORS;
window.isFutureRelation = isFutureRelation;
window.isFutureEntity = isFutureEntity;

// Re-render on theme change so edges/strokes pick up the new tokens
window.addEventListener('themechange', () => {
  // Defer to next frame so the CSS variables have been applied
  requestAnimationFrame(() => {
    if (window.__graphInstance) window.__graphInstance._render();
  });
});
