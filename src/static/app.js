// GraphRapping Demo UI

const API = '';
let charts = {};
let cyInstance = null;

// =============================================================================
// Navigation
// =============================================================================
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('sec-' + name).classList.add('active');
  document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
  event.target.classList.add('active');
  if (name === 'dashboard') loadDashboard();
  if (name === 'quarantine') loadQuarantine();
  if (name === 'recommend') initRecommendPanel();
  if (name === 'graph') initGraphPanel();
}

// =============================================================================
// Pipeline
// =============================================================================
async function runPipeline() {
  const limit = parseInt(document.getElementById('reviewLimit').value) || 100;
  document.getElementById('headerStatus').textContent = '파이프라인 실행 중...';
  try {
    const res = await fetch(API + '/api/pipeline/run', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ max_reviews: limit })
    });
    const data = await res.json();
    document.getElementById('headerStatus').textContent =
      `✅ 리뷰 ${data.reviews}건 / 상품 ${data.products}개 / 유저 ${data.users}명 / 신호 ${data.signals}개`;
    loadDashboard();
  } catch(e) {
    document.getElementById('headerStatus').textContent = '❌ 오류: ' + e.message;
  }
}

// =============================================================================
// Dashboard
// =============================================================================
async function loadDashboard() {
  try {
    const [summary, charts_data] = await Promise.all([
      fetch(API + '/api/dashboard/summary').then(r => r.json()),
      fetch(API + '/api/dashboard/charts').then(r => r.json()),
    ]);
    renderKPI(summary);
    renderCharts(charts_data);
  } catch(e) {
    document.getElementById('kpiGrid').innerHTML = '<div class="empty">파이프라인을 먼저 실행하세요</div>';
  }
}

function renderKPI(d) {
  document.getElementById('kpiGrid').innerHTML = `
    <div class="kpi-card"><div class="label">처리 리뷰</div><div class="value blue">${d.reviews_processed}</div></div>
    <div class="kpi-card"><div class="label">생성 신호</div><div class="value green">${d.total_signals}</div></div>
    <div class="kpi-card"><div class="label">격리 건수</div><div class="value ${d.total_quarantined > 0 ? 'yellow' : 'green'}">${d.total_quarantined}</div></div>
    <div class="kpi-card"><div class="label">서빙 상품</div><div class="value">${d.serving_products}</div></div>
    <div class="kpi-card"><div class="label">서빙 유저</div><div class="value">${d.serving_users}</div></div>
  `;
}

function renderCharts(d) {
  renderBarChart('chartSignal', d.signal_families, '신호 수', '#6366f1');
  renderBarChart('chartRelation', d.relation_types.slice(0, 15), '건수', '#22c55e');
  renderBarChart('chartBee', d.bee_attrs, '건수', '#f59e0b');
}

function renderBarChart(canvasId, data, label, color) {
  const ctx = document.getElementById(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map(d => d.name),
      datasets: [{ label, data: data.map(d => d.count), backgroundColor: color + '99', borderColor: color, borderWidth: 1 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { color: '#9ca3af', font: { size: 10 } } }, y: { ticks: { color: '#9ca3af' } } }
    }
  });
}

// =============================================================================
// Data Explorer
// =============================================================================
async function loadReviews() {
  const res = await fetch(API + '/api/reviews?size=50');
  const data = await res.json();
  const panel = document.getElementById('explorerContent');
  if (!data.items.length) { panel.innerHTML = '<div class="empty">리뷰 없음</div>'; return; }
  panel.innerHTML = `<table><thead><tr>
    <th>Review ID</th><th>매칭</th><th>상품</th><th>Entity</th><th>Fact</th><th>Signal</th><th>격리</th>
  </tr></thead><tbody>${data.items.map(r => `
    <tr class="clickable" onclick="showReviewDetail('${r.review_id}')">
      <td style="font-size:11px">${r.review_id.substring(0,30)}...</td>
      <td><span class="chip ${r.match_status === 'QUARANTINE' ? 'neg' : 'pos'}">${r.match_status || '-'}</span></td>
      <td>${r.matched_product_id || '-'}</td>
      <td>${r.entity_count}</td><td>${r.fact_count}</td><td>${r.signal_count}</td>
      <td>${r.quarantine_count}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function showReviewDetail(id) {
  const res = await fetch(API + '/api/reviews/' + encodeURIComponent(id));
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  dp.innerHTML = `<h3>리뷰 상세: ${id.substring(0,40)}...</h3><pre>${JSON.stringify(d, null, 2)}</pre>`;
}

async function loadProducts() {
  const res = await fetch(API + '/api/products');
  const data = await res.json();
  const panel = document.getElementById('explorerContent');
  panel.innerHTML = `<table><thead><tr>
    <th>상품명</th><th>브랜드</th><th>카테고리</th><th>리뷰수(all)</th><th>Top BEE</th>
  </tr></thead><tbody>${data.items.map(p => `
    <tr class="clickable" onclick="showProductDetail('${p.product_id}')">
      <td>${productDisplayName(p)}</td><td>${p.brand_name || '-'}</td><td>${p.category_name || '-'}</td>
      <td>${p.review_count_all || 0}</td>
      <td>${(p.top_bee_attr_ids||[]).slice(0,2).map(a => a.id ? a.id.split(':').pop() : '').join(', ')}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function showProductDetail(id) {
  const res = await fetch(API + '/api/products/' + id);
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  const sp = d.serving_profile || {};
  const name = sp.representative_product_name ? `${sp.brand_name || ''} ${sp.representative_product_name}` : id;
  dp.innerHTML = `<h3>상품 상세: ${name}</h3><pre>${JSON.stringify(d, null, 2)}</pre>`;
}

async function loadUsers() {
  const res = await fetch(API + '/api/users');
  const data = await res.json();
  const panel = document.getElementById('explorerContent');
  panel.innerHTML = `<table><thead><tr>
    <th>User ID</th><th>연령대</th><th>성별</th><th>피부타입</th><th>고민</th><th>선호 브랜드</th>
  </tr></thead><tbody>${data.items.map(u => `
    <tr class="clickable" onclick="showUserDetail('${u.user_id}')">
      <td>${u.user_id}</td><td>${u.age_band || '-'}</td><td>${u.gender || '-'}</td>
      <td>${u.skin_type || '-'}</td>
      <td>${(u.concern_ids||[]).slice(0,2).map(c => c.id ? c.id.split(':').pop() : '').join(', ')}</td>
      <td>${(u.preferred_brand_ids||[]).slice(0,2).map(b => b.id ? b.id.split(':').pop() : '').join(', ')}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function showUserDetail(id) {
  const res = await fetch(API + '/api/users/' + id);
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  dp.innerHTML = `<h3>유저 상세: ${id}</h3><pre>${JSON.stringify(d, null, 2)}</pre>`;
}

// =============================================================================
// Product display name helper
// =============================================================================
function productDisplayName(p) {
  const name = p.representative_product_name || p.product_name || '';
  const brand = p.brand_name || '';
  if (name && brand) return `${brand} ${name}`;
  if (name) return name;
  return p.product_id;
}

// =============================================================================
// Recommendation Tester
// =============================================================================
const DEFAULT_WEIGHTS = {
  keyword_match: 0.20, residual_bee_attr_match: 0.09, context_match: 0.11,
  concern_fit: 0.11, ingredient_match: 0.08, brand_match_conf_weighted: 0.07,
  goal_fit_master: 0.05, goal_fit_review_signal: 0.05,
  category_affinity: 0.05, freshness_boost: 0.04,
  skin_type_fit: 0.06, purchase_loyalty_score: 0.04, novelty_bonus: 0.02,
  owned_family_penalty: 0.04, repurchase_family_affinity: 0.03,
  tool_alignment: 0.02, coused_product_bonus: 0.02,
};
const WEIGHT_META = {
  keyword_match:              { label: '키워드 일치',      group: 'core',    desc: '리뷰에서 추출된 구체 키워드(촉촉, 물광 등)와 유저 선호 키워드의 겹침. 가장 핵심적인 매칭 축.' },
  residual_bee_attr_match:    { label: '잔여 BEE속성',    group: 'core',    desc: '키워드로 이미 커버되지 않은 상위 BEE 속성(제형, 발림성 등)의 추가 매칭. 키워드와 중복 카운트를 방지하는 잔여분만 반영.' },
  context_match:              { label: '맥락 일치',       group: 'core',    desc: '사용 맥락(아침, 세안 후, 여름 등)이 유저 선호 맥락과 겹치는 정도.' },
  concern_fit:                { label: '고민 적합도',      group: 'core',    desc: '유저 피부 고민(건조, 모공, 트러블 등)을 제품 리뷰 시그널이 얼마나 다루는지.' },
  ingredient_match:           { label: '성분 일치',       group: 'core',    desc: '유저가 선호하는 성분(히알루론산, 나이아신아마이드 등)이 제품에 포함된 정도.' },
  brand_match_conf_weighted:  { label: '브랜드 신뢰',      group: 'core',    desc: '유저 선호 브랜드와 제품 브랜드 일치 여부. 구매 이력 기반이면 더 강한 신뢰도 반영.' },
  goal_fit_master:            { label: '목표(제품truth)',  group: 'core',    desc: '제품 마스터 데이터에 등록된 주요 효능(보습, 미백 등)과 유저 케어 목표의 일치.' },
  goal_fit_review_signal:     { label: '목표(리뷰시그널)', group: 'core',    desc: '리뷰에서 추출된 효능/목표 시그널과 유저 케어 목표의 일치. 제품truth와 별개로 리뷰 기반 근거.' },
  category_affinity:          { label: '카테고리',        group: 'core',    desc: '유저 선호 카테고리(에센스, 크림, 쿠션 등)와 제품 카테고리 일치.' },
  freshness_boost:            { label: '최신성',          group: 'meta',    desc: '최근 30일 리뷰 수 기반. 리뷰가 활발한 제품에 가산점. (10건↑=1.0, 3건↑=0.6, 1건↑=0.3)' },
  skin_type_fit:              { label: '피부타입 적합',    group: 'meta',    desc: '유저 피부타입(건성/지성/복합/민감)과 제품 리뷰의 고민 시그널 간 궁합. 건성에 보습 긍정 → 가산, 끈적 부정 → 감점.' },
  purchase_loyalty_score:     { label: '구매 충성도',      group: 'personal', desc: '유저가 해당 브랜드 제품을 재구매한 이력이 있으면 1.0, 최근 구매면 0.5.' },
  novelty_bonus:              { label: '신규성 보너스',    group: 'personal', desc: '유저가 아직 모르는 브랜드/제품일수록 높은 점수. 이미 보유한 제품=0, 같은 패밀리=0.2, 아는 브랜드=0.5, 처음=1.0.' },
  owned_family_penalty:       { label: '보유패밀리 감점',  group: 'personal', desc: '유저가 이미 같은 variant family(같은 제품군의 다른 호수/용량) 제품을 보유하면 감점. 중복 추천 방지.' },
  repurchase_family_affinity: { label: '재구매패밀리 가산', group: 'personal', desc: '유저가 재구매한 패밀리의 다른 SKU에 가산. "이 라인 좋아하시네요" 식의 확장 추천.' },
  tool_alignment:             { label: '도구 일치',       group: 'coused',  desc: '제품과 함께 언급되는 도구(퍼프, 브러시 등)가 유저 선호 도구와 겹칠 때 가산.' },
  coused_product_bonus:       { label: '함께쓰는제품',     group: 'coused',  desc: '유저가 보유한 제품과 자주 함께 쓰이는 제품에 가산. 루틴/번들 추천 근거.' },
};
const GROUP_LABELS = {
  core: '핵심 매칭 (유저↔제품 시그널 겹침)',
  meta: '메타 시그널 (제품 상태)',
  personal: '개인화 (구매/보유 이력 기반)',
  coused: '함께쓰기 (루틴/번들)',
};
const MODE_DESC = {
  explore: '카테고리 불일치 시 감점만 적용 (penalty 0.3). 다양한 카테고리 탐색 가능. 기본 모드.',
  strict: '유저 선호 카테고리와 불일치하면 점수를 0으로 만듦. 정확한 카테고리 내에서만 추천.',
  compare: '카테고리 제한 없음. 비교/대안 추천용. 다른 카테고리 제품도 자유롭게 노출.',
};

async function initRecommendPanel() {
  try {
    const users = await fetch(API + '/api/users').then(r => r.json());
    const sel = document.getElementById('recUser');
    sel.innerHTML = users.items.map(u => `<option value="${u.user_id}">${u.user_id} (${u.skin_type||''}/${u.gender||''})</option>`).join('');
  } catch(e) {}
  // Mode description
  const modeDesc = document.getElementById('modeDesc');
  const modeSelect = document.getElementById('recMode');
  if (modeDesc) modeDesc.textContent = MODE_DESC[modeSelect.value] || '';
  modeSelect.onchange = () => { if (modeDesc) modeDesc.textContent = MODE_DESC[modeSelect.value] || ''; };

  // Weight sliders grouped
  const container = document.getElementById('weightSliders');
  const groups = {};
  for (const [k, v] of Object.entries(DEFAULT_WEIGHTS)) {
    const meta = WEIGHT_META[k] || {};
    const g = meta.group || 'other';
    if (!groups[g]) groups[g] = [];
    groups[g].push({ key: k, value: v, meta });
  }
  container.innerHTML = Object.entries(groups).map(([g, items]) => `
    <div class="weight-group">
      <div class="weight-group-label">${GROUP_LABELS[g] || g}</div>
      ${items.map(({ key, value, meta }) => `
        <div class="slider-group" title="${meta.desc || ''}">
          <label>
            <span class="weight-name">${meta.label || key}</span>
            <span id="w_${key}_val">${value.toFixed(2)}</span>
          </label>
          <input type="range" min="0" max="50" value="${Math.round(value*100)}" id="w_${key}"
            oninput="document.getElementById('w_${key}_val').textContent=(this.value/100).toFixed(2); showWeightDesc('${key}')">
          <div class="weight-desc" id="desc_${key}" style="display:none">${meta.desc || ''}</div>
        </div>
      `).join('')}
    </div>
  `).join('');
}

function showWeightDesc(key) {
  const el = document.getElementById('desc_' + key);
  if (el) el.style.display = 'block';
}

async function runRecommend() {
  const userId = document.getElementById('recUser').value;
  if (!userId) return;

  // Collect weights from sliders — check if any were changed from default
  const weights = {};
  let customized = false;
  for (const k of Object.keys(DEFAULT_WEIGHTS)) {
    const el = document.getElementById('w_' + k);
    if (!el) continue;
    const val = parseInt(el.value) / 100;
    weights[k] = val;
    if (Math.abs(val - DEFAULT_WEIGHTS[k]) > 0.005) customized = true;
  }

  const body = {
    user_id: userId,
    mode: document.getElementById('recMode').value,
    top_k: 10,
    weights: customized ? weights : null,  // null → server uses YAML config
    shrinkage_k: parseFloat(document.getElementById('shrinkageK').value),
    diversity_weight: parseInt(document.getElementById('diversityW').value) / 100,
  };

  const res = await fetch(API + '/api/recommend', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const data = await res.json();
  renderRecommendResults(data);
}

function renderRecommendResults(data) {
  document.getElementById('recMeta').innerHTML = `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="label">후보군</div><div class="value blue">${data.candidate_count}</div></div>
      <div class="kpi-card"><div class="label">최종 결과</div><div class="value green">${data.results.length}</div></div>
      <div class="kpi-card"><div class="label">다음 질문</div><div class="value" style="font-size:13px">${data.next_question ? data.next_question.question : '-'}</div></div>
    </div>`;

  const container = document.getElementById('recResults');
  if (!data.results.length) {
    container.innerHTML = '<div class="empty">추천 결과 없음 (concept overlap 부족)</div>';
    return;
  }
  container.innerHTML = data.results.map(r => `
    <div class="rec-card">
      <div class="rank">#${r.rank}</div>
      <div>
        <strong>${r.product.representative_product_name ? (r.product.brand_name || '') + ' ' + r.product.representative_product_name : r.product_id}</strong>
        <span class="score">점수: ${r.final_score.toFixed(4)} (raw: ${r.raw_score.toFixed(4)}, shrink: ${r.shrinked_score.toFixed(4)}, diversity: ${r.diversity_bonus >= 0 ? '+' : ''}${r.diversity_bonus.toFixed(4)})</span>
        <div class="explanation">${r.explanation || '설명 없음'}</div>
        ${r.explanation_paths.length ? '<h3 style="margin-top:8px">설명 경로</h3>' + r.explanation_paths.map(p => `
          <div class="path-row">
            <span class="chip ner">${p.user_edge}</span>
            <span class="arrow">→</span>
            <span class="chip bee">${p.id.split(':').pop()}</span>
            <span class="arrow">→</span>
            <span class="chip rel">${p.product_edge}</span>
            <span style="margin-left:8px;color:var(--green)">(+${p.contribution.toFixed(3)})</span>
          </div>
        `).join('') : ''}
        <div class="hooks" style="margin-top:8px">
          <span><span class="label">🔍 탐색:</span> ${r.hooks.discovery}</span>
          <span><span class="label">🤔 고려:</span> ${r.hooks.consideration}</span>
          <span><span class="label">🎯 전환:</span> ${r.hooks.conversion}</span>
        </div>
        ${r.overlap_concepts.length ? `<div style="margin-top:8px">${r.overlap_concepts.map(c => `<span class="chip bee">${c}</span>`).join('')}</div>` : ''}
      </div>
    </div>
  `).join('');
}

// =============================================================================
// Graph Viewer
// =============================================================================
async function initGraphPanel() {
  try {
    const [products, users] = await Promise.all([
      fetch(API + '/api/products').then(r => r.json()),
      fetch(API + '/api/users').then(r => r.json()),
    ]);
    const sel = document.getElementById('graphTarget');
    sel.innerHTML = '<option value="">대상 선택...</option>'
      + products.items.map(p => `<option value="product:${p.product_id}">🏷️ ${productDisplayName(p)}</option>`).join('')
      + users.items.map(u => `<option value="user:${u.user_id}">👤 ${u.user_id}</option>`).join('');
  } catch(e) {}
}

async function loadGraph() {
  const target = document.getElementById('graphTarget').value;
  if (!target) return;
  const [type, id] = target.split(':');
  const url = type === 'product' ? `/api/graphs/product/${id}` : `/api/graphs/user/${id}`;
  const data = await fetch(API + url).then(r => r.json());
  renderGraph(data);
}

const TYPE_COLORS = {
  product: '#6366f1', user: '#8b5cf6', bee_attr: '#f59e0b', keyword: '#eab308',
  context: '#22c55e', concern_pos: '#10b981', concern_neg: '#ef4444',
  tool: '#3b82f6', comparison: '#ec4899', coused: '#f97316',
  brand: '#a78bfa', category: '#67e8f9', ingredient: '#34d399',
  avoid_ingredient: '#f87171', concern: '#fbbf24', goal: '#4ade80',
};

function renderGraph(data) {
  const container = document.getElementById('graph-container');
  if (cyInstance) cyInstance.destroy();
  const elements = [];
  data.nodes.forEach(n => {
    elements.push({ data: {
      id: n.id,
      label: n.label || n.id,
      type: n.type,
      main: n.main,
      color: TYPE_COLORS[n.type] || '#6b7280',
      size: n.main ? 40 : 25,
    }});
  });
  data.edges.forEach(e => {
    elements.push({ data: {
      source: e.source,
      target: e.target,
      label: e.label,
      weight: Math.max(1, Math.min((e.weight || 1) * 3, 6)),
    }});
  });
  cyInstance = cytoscape({
    container,
    elements,
    style: [
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
        'width': 'data(weight)',
        'line-color': '#4b5563',
        'target-arrow-color': '#4b5563',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'label': 'data(label)',
        'font-size': 8,
        'color': '#9ca3af',
        'text-rotation': 'autorotate',
        'text-outline-color': '#0f1117',
        'text-outline-width': 1,
      }},
    ],
    layout: { name: 'cose', padding: 40, nodeRepulsion: 8000, idealEdgeLength: 120 },
  });
}

// =============================================================================
// Quarantine
// =============================================================================
async function loadQuarantine() {
  try {
    const [summary, entries] = await Promise.all([
      fetch(API + '/api/quarantine/summary').then(r => r.json()),
      fetch(API + '/api/quarantine/entries?size=50').then(r => r.json()),
    ]);
    const kpi = document.getElementById('quarantineKpi');
    const byTable = summary.by_table || {};
    kpi.innerHTML = Object.entries(byTable).map(([k, v]) => `
      <div class="kpi-card"><div class="label">${k.replace('quarantine_','')}</div><div class="value yellow">${v}</div></div>
    `).join('') || '<div class="kpi-card"><div class="label">격리 없음</div><div class="value green">0</div></div>';

    const list = document.getElementById('quarantineList');
    if (!entries.items.length) {
      list.innerHTML = '<div class="empty">격리 항목 없음</div>';
    } else {
      list.innerHTML = `<table><thead><tr><th>타입</th><th>사유</th><th>상태</th><th>상세</th></tr></thead>
        <tbody>${entries.items.map(e => `<tr>
          <td><span class="chip rel">${(e.table||'').replace('quarantine_','')}</span></td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${e.reason || '-'}</td>
          <td>${e.status || '-'}</td>
          <td><button class="btn btn-sm" onclick='alert(JSON.stringify(${JSON.stringify(e)},null,2))'>JSON</button></td>
        </tr>`).join('')}</tbody></table>`;
    }
  } catch(e) {
    document.getElementById('quarantineKpi').innerHTML = '<div class="empty">데이터 로드 필요</div>';
  }
}

// =============================================================================
// Init
// =============================================================================
loadDashboard();
