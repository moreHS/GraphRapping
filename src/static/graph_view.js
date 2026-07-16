// GraphRapping -- reusable cytoscape graph renderer (Phase 6 Track A3)
//
// window.GraphView.render(container, data, opts) / destroy(container)
// manages ONE cytoscape instance per container element, keyed by the element
// itself in a WeakMap. Re-rendering the same container destroys the previous
// instance first, so the Graph Viewer tab and every recommendation card's
// inline "why this" mini-view stay independent (no shared global instance).
//
// data shape (unchanged from the previous single-container renderGraph):
//   { nodes: [{id, label, type, main, ...}],
//     edges: [{source, target, label, weight?, width?, color?}] }
//
// Node color comes from TYPE_COLORS[node.type] (gray fallback), size from
// node.main. Edge width: explicit `width` (px) wins; otherwise it is derived
// from `weight` exactly as before (back-compat with the /api/graphs/* builder).
// Edge `color` overrides the default line color -- the recommendation inline
// subgraph uses it to flag negative-contribution paths in red.
(function () {
  const TYPE_COLORS = {
    product: '#6366f1', user: '#8b5cf6', bee_attr: '#f59e0b', keyword: '#eab308',
    context: '#22c55e', concern_pos: '#10b981', concern_neg: '#ef4444',
    tool: '#3b82f6', comparison: '#ec4899', coused: '#f97316',
    brand: '#a78bfa', category: '#67e8f9', ingredient: '#34d399', goal: '#4ade80',
    avoid_ingredient: '#f87171', concern: '#fbbf24',
  };
  const DEFAULT_NODE_COLOR = '#6b7280';
  const DEFAULT_EDGE_COLOR = '#4b5563';

  // container element -> cytoscape instance (WeakMap: detached containers are
  // GC-eligible even if a caller forgets to destroy).
  const instances = new WeakMap();

  function edgeWidth(e) {
    if (typeof e.width === 'number' && !Number.isNaN(e.width)) return e.width;
    // Back-compat with the graph-viewer builder, which supplies `weight`.
    return Math.max(1, Math.min((e.weight || 1) * 3, 6));
  }

  function toElements(data) {
    const elements = [];
    (data.nodes || []).forEach(n => {
      elements.push({ data: {
        id: n.id,
        label: n.label || n.id,
        type: n.type,
        main: n.main,
        color: TYPE_COLORS[n.type] || DEFAULT_NODE_COLOR,
        size: n.main ? 40 : 25,
      }});
    });
    (data.edges || []).forEach(e => {
      elements.push({ data: {
        source: e.source,
        target: e.target,
        label: e.label,
        width: edgeWidth(e),
        lineColor: e.color || DEFAULT_EDGE_COLOR,
        // Phase 8 G2: similarity edges carry their shared-attribute evidence so
        // the hover/tap tooltip can answer "why are these two connected".
        sharedAxes: Array.isArray(e.shared_axes) ? e.shared_axes : null,
        score: (typeof e.score === 'number') ? e.score : null,
      }});
    });
    return elements;
  }

  // Phase 8 G2: format a SHARES_ATTRIBUTE edge's shared_axes into a readable line
  // e.g. "공유 속성: 보습좋음(IDF 2.1) · 저자극(IDF 1.4)".
  function formatSharedAxes(axes) {
    if (!Array.isArray(axes) || !axes.length) return '';
    const parts = axes.map(a => {
      const label = (a && (a.label || (a.node_key || '').split(':').pop())) || '';
      const idf = (a && typeof a.idf === 'number') ? ` (IDF ${a.idf.toFixed(1)})` : '';
      return `${label}${idf}`;
    }).filter(Boolean);
    return parts.length ? ('공유 속성: ' + parts.join(' · ')) : '';
  }

  // Single shared tooltip element (created lazily; reused across instances).
  let tooltipEl = null;
  function ensureTooltip() {
    if (tooltipEl) return tooltipEl;
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'graph-edge-tooltip';
    Object.assign(tooltipEl.style, {
      position: 'fixed', zIndex: '9999', pointerEvents: 'none', display: 'none',
      maxWidth: '300px', padding: '6px 10px', borderRadius: '6px',
      fontSize: '11px', lineHeight: '1.5', background: '#1f2430', color: '#e4e6eb',
      border: '1px solid #c084fc', boxShadow: '0 2px 10px rgba(0,0,0,0.45)',
    });
    document.body.appendChild(tooltipEl);
    return tooltipEl;
  }
  function hideTooltip() { if (tooltipEl) tooltipEl.style.display = 'none'; }
  function showTooltipFor(evt) {
    const text = formatSharedAxes(evt.target.data('sharedAxes'));
    if (!text) { hideTooltip(); return; }
    const tip = ensureTooltip();
    const score = evt.target.data('score');
    tip.textContent = (typeof score === 'number') ? `${text}  ·  score ${score.toFixed(2)}` : text;
    tip.style.display = 'block';
    const oe = evt.originalEvent;
    if (oe && typeof oe.clientX === 'number') {
      tip.style.left = (oe.clientX + 12) + 'px';
      tip.style.top = (oe.clientY + 12) + 'px';
    }
  }

  const STYLE = [
    { selector: 'node', style: {
      'label': 'data(label)',
      'font-size': 10,
      'color': '#e4e6eb',
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'background-color': 'data(color)',
      'width': 'data(size)',
      'height': 'data(size)',
      'text-outline-color': '#0f1117',
      'text-outline-width': 2,
    }},
    { selector: 'edge', style: {
      'width': 'data(width)',
      'line-color': 'data(lineColor)',
      'target-arrow-color': 'data(lineColor)',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'label': 'data(label)',
      'font-size': 8,
      'color': '#9ca3af',
      'text-rotation': 'autorotate',
      'text-outline-color': '#0f1117',
      'text-outline-width': 1,
    }},
    // Phase 8 G2: product-product similarity edge — undirected (no arrow),
    // dashed, and a distinct purple so it reads apart from the directed
    // product->attribute edges.
    { selector: 'edge[label = "SHARES_ATTRIBUTE"]', style: {
      'line-color': '#c084fc',
      'target-arrow-shape': 'none',
      'source-arrow-shape': 'none',
      'line-style': 'dashed',
    }},
  ];

  const DEFAULT_LAYOUT = { name: 'cose', padding: 40, nodeRepulsion: 8000, idealEdgeLength: 120 };

  function render(container, data, opts) {
    if (!container) return null;
    opts = opts || {};
    destroy(container);  // idempotent: replace any prior instance on this element
    const cy = cytoscape({
      container,
      elements: toElements(data || {}),
      style: STYLE,
      layout: Object.assign({}, DEFAULT_LAYOUT, opts.layout || {}),
    });
    // Phase 8 G2: reveal the shared-attribute evidence on hover/tap of a
    // similarity edge (no tooltip infra existed before). Scoped to
    // SHARES_ATTRIBUTE edges, so directed edges are unaffected.
    const SIM_SELECTOR = 'edge[label = "SHARES_ATTRIBUTE"]';
    cy.on('mouseover', SIM_SELECTOR, showTooltipFor);
    cy.on('mousemove', SIM_SELECTOR, showTooltipFor);
    cy.on('mouseout', SIM_SELECTOR, hideTooltip);
    cy.on('tap', SIM_SELECTOR, showTooltipFor);
    cy.on('tap', evt => { if (evt.target === cy) hideTooltip(); });
    instances.set(container, cy);
    return cy;
  }

  function destroy(container) {
    if (!container) return;
    const cy = instances.get(container);
    if (cy) {
      hideTooltip();
      cy.destroy();
      instances.delete(container);
    }
  }

  window.GraphView = { render, destroy, TYPE_COLORS };
})();
