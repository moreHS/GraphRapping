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
      }});
    });
    return elements;
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
    instances.set(container, cy);
    return cy;
  }

  function destroy(container) {
    if (!container) return;
    const cy = instances.get(container);
    if (cy) {
      cy.destroy();
      instances.delete(container);
    }
  }

  window.GraphView = { render, destroy, TYPE_COLORS };
})();
