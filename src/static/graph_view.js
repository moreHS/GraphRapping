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
    // F5 full-graph concept types (rare user-demographic concepts).
    skin_type: '#f472b6', skin_tone: '#fb923c',
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

  // F5 full-graph node-label abbreviation (plan §F5): user = pseudonym tail,
  // product = truncated name, concept = label as-is. Only applied in full mode;
  // product/user/rec subgraphs keep their full labels.
  function abbrevLabel(n) {
    const label = n.label || n.id || '';
    if (n.type === 'user') return '…' + String(n.id || '').slice(-4);
    if (n.type === 'product') return label.length > 12 ? label.slice(0, 12) + '…' : label;
    return label;
  }

  function toElements(data, opts) {
    const full = !!(opts && opts.full);
    const elements = [];
    (data.nodes || []).forEach(n => {
      elements.push({ data: {
        id: n.id,
        label: full ? abbrevLabel(n) : (n.label || n.id),
        // Un-abbreviated label + brand ride along for the node hover tooltip:
        // the full view truncates the visible label, so the tooltip restores
        // the whole name; brand is present only on full-view product nodes.
        fullLabel: n.label || n.id,
        brand: n.brand || null,
        type: n.type,
        main: n.main,
        color: TYPE_COLORS[n.type] || DEFAULT_NODE_COLOR,
        size: n.main ? 40 : (full ? 18 : 25),
      }});
    });
    (data.edges || []).forEach(e => {
      elements.push({ data: {
        source: e.source,
        target: e.target,
        label: e.label,
        // F5: edge family (product_concept/user_concept/owns/shares_attribute)
        // drives the full-graph family toggles.
        family: e.family || null,
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
      whiteSpace: 'pre-line',  // node tooltip uses "\n" for its brand·id line
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

  // Node hover/tap tooltip (all graph views). Reuses the shared tooltip element
  // above; node vs edge stay on separate cy selectors so they never collide.
  // The full view truncates node labels, so the tooltip surfaces the whole
  // label + a type tag. Product nodes additionally show brand + id, but only
  // when the node carries a brand (full-view product nodes do; per-product and
  // rec-subgraph product nodes do not). User nodes stay pseudonym-only — no
  // profile field ever rides the node payload.
  const NODE_TYPE_LABELS = {
    product: '상품', user: '유저', brand: '브랜드', category: '카테고리',
    ingredient: '성분', goal: '목표', bee_attr: 'BEE속성', keyword: '키워드',
    context: '맥락', concern: '고민', concern_pos: '고민(긍정)',
    concern_neg: '고민(부정)', tool: '도구', coused: '함께쓰는 제품',
    comparison: '비교', avoid_ingredient: '기피 성분',
    skin_type: '피부타입', skin_tone: '피부톤',
  };
  function formatNodeTooltip(node) {
    const type = node.data('type') || '';
    const label = node.data('fullLabel') || node.data('label') || node.data('id') || '';
    if (type === 'user') return `[유저] ${label}`;
    if (type === 'product') {
      const brand = node.data('brand');
      return brand
        ? `[상품] ${label}\n브랜드 ${brand} · id ${node.data('id')}`
        : `[상품] ${label}`;
    }
    return `[${NODE_TYPE_LABELS[type] || type || '노드'}] ${label}`;
  }
  function showNodeTooltipFor(evt) {
    const tip = ensureTooltip();
    tip.textContent = formatNodeTooltip(evt.target);
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

  // F5 full-graph mode: built-in cose only (fcose is NOT installed — plan §F5).
  // Perf tuning (Fable perf round): the ingredient-excluded default (~820 nodes /
  // ~3.4k edges) settles in ~4.5s with numIter=150 (headless measured 4.46s;
  // headless≈browser at a 1.01x calibrated ratio vs the 34.2s browser baseline).
  // cose repulsion is O(numIter·n²), so numIter is the dominant lever; animate:
  // false + randomize:true skip inter-step renders and warm-start from a spread.
  const FULL_LAYOUT = {
    name: 'cose', padding: 40, nodeRepulsion: 9000, idealEdgeLength: 90,
    animate: false, randomize: true, numIter: 150,
  };
  // Extra stylesheet appended (per-instance) only for the full view: edge labels
  // hidden until zoomed in / focused; non-neighbourhood elements dim on focus.
  const FULL_EXTRA_STYLE = [
    { selector: 'edge', style: { 'text-opacity': 0 } },
    { selector: 'edge.gv-labeled', style: { 'text-opacity': 1 } },
    { selector: 'edge.gv-hl', style: { 'text-opacity': 1 } },
    { selector: '.gv-dim', style: { 'opacity': 0.1, 'text-opacity': 0 } },
    { selector: 'node.gv-hl', style: { 'border-width': 2, 'border-color': '#f8fafc' } },
  ];
  const EDGE_LABEL_ZOOM = 1.6;   // reveal edge labels past this zoom
  const DBLTAP_MS = 300;         // manual double-tap window (cross-version safe)

  function focusNode(cy, node) {
    const hood = node.closedNeighborhood();
    cy.batch(() => {
      cy.elements().addClass('gv-dim').removeClass('gv-hl');
      hood.removeClass('gv-dim').addClass('gv-hl');
    });
    cy.animate({ fit: { eles: hood, padding: 80 } }, { duration: 300 });
  }

  function clearFocus(cy) {
    cy.batch(() => cy.elements().removeClass('gv-dim gv-hl'));
  }

  // Double-click a node -> ego view: only that node + neighbours, re-laid out.
  // Original full-layout positions are snapshotted once so restoreFull() can
  // put every node back exactly where it was.
  function egoLayout(cy, node) {
    if (!cy.scratch('_gvPos')) {
      const pos = {};
      cy.nodes().forEach(n => { const p = n.position(); pos[n.id()] = { x: p.x, y: p.y }; });
      cy.scratch('_gvPos', pos);
    }
    const hood = node.closedNeighborhood();
    cy.batch(() => {
      cy.elements().removeClass('gv-dim gv-hl').style('display', 'none');
      hood.style('display', 'element');
      node.addClass('gv-hl');
    });
    hood.layout(Object.assign({}, FULL_LAYOUT, { padding: 40 })).run();
    cy.fit(hood, 60);
  }

  function wireFullInteractions(cy, opts) {
    let labeled = false;
    cy.on('zoom', () => {
      const show = cy.zoom() >= EDGE_LABEL_ZOOM;
      if (show !== labeled) { labeled = show; cy.edges().toggleClass('gv-labeled', show); }
    });
    let lastTap = 0, lastId = null;
    cy.on('tap', 'node', evt => {
      const now = Date.now(), id = evt.target.id();
      if (now - lastTap < DBLTAP_MS && lastId === id) {
        egoLayout(cy, evt.target);
        if (opts.onEgo) opts.onEgo(id);
        lastTap = 0; lastId = null;
      } else {
        focusNode(cy, evt.target);
        if (opts.onFocus) opts.onFocus(id);
        lastTap = now; lastId = id;
      }
    });
    cy.on('tap', evt => {
      if (evt.target === cy) { clearFocus(cy); if (opts.onFocus) opts.onFocus(null); }
    });
  }

  // Toggle node types / edge families on the current full-graph instance.
  // Hiding a node type also hides any edge touching it (no dangling stubs).
  function applyFullVisibility(container, hiddenTypes, hiddenFamilies) {
    const cy = instances.get(container);
    if (!cy) return;
    const ht = hiddenTypes || new Set();
    const hf = hiddenFamilies || new Set();
    cy.batch(() => {
      cy.nodes().forEach(n => n.style('display', ht.has(n.data('type')) ? 'none' : 'element'));
      cy.edges().forEach(e => {
        const hidden = hf.has(e.data('family'))
          || ht.has(e.source().data('type')) || ht.has(e.target().data('type'));
        e.style('display', hidden ? 'none' : 'element');
      });
    });
  }

  // "전체로 돌아가기": un-hide everything, restore snapshotted positions, refit.
  function restoreFull(container) {
    const cy = instances.get(container);
    if (!cy) return;
    const pos = cy.scratch('_gvPos');
    cy.batch(() => {
      cy.elements().removeClass('gv-dim gv-hl').style('display', 'element');
      if (pos) cy.nodes().forEach(n => { if (pos[n.id()]) n.position(pos[n.id()]); });
    });
    cy.fit(cy.elements(), 40);
  }

  function render(container, data, opts) {
    if (!container) return null;
    opts = opts || {};
    destroy(container);  // idempotent: replace any prior instance on this element
    const full = opts.mode === 'full';
    const cy = cytoscape({
      container,
      elements: toElements(data || {}, { full }),
      // Per-instance style: the full view appends FULL_EXTRA_STYLE so the shared
      // STYLE (used by product/user/rec subgraphs) is never mutated.
      style: full ? STYLE.concat(FULL_EXTRA_STYLE) : STYLE,
      layout: full
        ? Object.assign({}, FULL_LAYOUT, opts.layout || {})
        : Object.assign({}, DEFAULT_LAYOUT, opts.layout || {}),
    });
    // Phase 8 G2: reveal the shared-attribute evidence on hover/tap of a
    // similarity edge (no tooltip infra existed before). Scoped to
    // SHARES_ATTRIBUTE edges, so directed edges are unaffected.
    const SIM_SELECTOR = 'edge[label = "SHARES_ATTRIBUTE"]';
    cy.on('mouseover', SIM_SELECTOR, showTooltipFor);
    cy.on('mousemove', SIM_SELECTOR, showTooltipFor);
    cy.on('mouseout', SIM_SELECTOR, hideTooltip);
    cy.on('tap', SIM_SELECTOR, showTooltipFor);
    // Node hover/tap tooltip — every view, separate selector from the edge one.
    cy.on('mouseover', 'node', showNodeTooltipFor);
    cy.on('mousemove', 'node', showNodeTooltipFor);
    cy.on('mouseout', 'node', hideTooltip);
    cy.on('tap', 'node', showNodeTooltipFor);
    cy.on('tap', evt => { if (evt.target === cy) hideTooltip(); });
    if (full) {
      // Layout telemetry for the perf gate (main session re-measures in-browser).
      const perf = (typeof window !== 'undefined' && window.performance) || Date;
      const t0 = perf.now();
      cy.one('layoutstop', () => {
        console.log('[GraphView] full layout ' + Math.round(perf.now() - t0) + 'ms'
          + ' — nodes ' + cy.nodes().length + ' edges ' + cy.edges().length);
      });
      wireFullInteractions(cy, opts);
    }
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

  window.GraphView = { render, destroy, TYPE_COLORS, applyFullVisibility, restoreFull };
})();
