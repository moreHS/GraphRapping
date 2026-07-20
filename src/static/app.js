// GraphRapping Demo UI

const API = '';
let charts = {};

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
// Developer Mode (Phase 6 Track A2)
// =============================================================================
// Default OFF: hides technical recommendation controls (weight sliders, mode
// select, shrinkage/diversity sliders, result-card score-layer breakdown,
// raw/shrink score numbers) and the header's pipeline-run controls, leaving
// only the evidence-first UI (evidence chips, explanation, explanation
// paths+snippets, source trust, next question, hooks).
//
// State: localStorage(gr_dev_mode) + URL `?dev=1` (which also persists to
// localStorage, so a shared "?dev=1" link keeps working on later reloads
// without the query param). Visibility is pure CSS
// (body.dev-mode-on toggles .dev-only/.user-only, see app.css), so toggling
// applies instantly to already-rendered content (e.g. recommend result
// cards already on screen) with no re-render needed.
const DEV_MODE_STORAGE_KEY = 'gr_dev_mode';

function isDevMode() {
  return localStorage.getItem(DEV_MODE_STORAGE_KEY) === '1';
}

function setDevMode(enabled) {
  localStorage.setItem(DEV_MODE_STORAGE_KEY, enabled ? '1' : '0');
  applyDevModeUI();
}

function toggleDevMode() {
  setDevMode(!isDevMode());
}

function applyDevModeUI() {
  const enabled = isDevMode();
  document.body.classList.toggle('dev-mode-on', enabled);
  const btn = document.getElementById('devModeToggle');
  if (btn) {
    btn.classList.toggle('btn-primary', enabled);
    btn.textContent = enabled ? '🛠 개발자 모드 ON' : '🛠 개발자';
  }
}

function initDevMode() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('dev') === '1') {
    setDevMode(true);  // URL override also persists to localStorage
  } else {
    applyDevModeUI();
  }
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
    updateHeaderStatus(summary);
    renderKPI(summary);
    renderCharts(charts_data);
  } catch(e) {
    document.getElementById('kpiGrid').innerHTML = '<div class="empty">파이프라인을 먼저 실행하세요</div>';
  }
}

function updateHeaderStatus(summary) {
  const el = document.getElementById('headerStatus');
  if (!el) return;
  if (!summary || !summary.loaded) {
    el.textContent = '데이터 미로드';
    return;
  }
  el.textContent = `리뷰 ${fmtCount(summary.reviews_processed)}건 / 상품 ${fmtCount(summary.serving_products)}개 / 유저 ${fmtCount(summary.serving_users)}명 / 신호 ${fmtCount(summary.total_signals)}개`;
}

function renderKPI(d) {
  document.getElementById('kpiGrid').innerHTML = `
    <div class="kpi-card"><div class="label">처리 리뷰</div><div class="value blue">${d.reviews_processed}</div></div>
    <div class="kpi-card"><div class="label">생성 신호</div><div class="value green">${d.total_signals}</div></div>
    <div class="kpi-card"><div class="label">격리 건수</div><div class="value ${d.total_quarantined > 0 ? 'yellow' : 'green'}">${d.total_quarantined}</div></div>
    <div class="kpi-card"><div class="label">서빙 상품</div><div class="value">${d.serving_products}</div></div>
    <div class="kpi-card"><div class="label">서빙 유저</div><div class="value">${d.serving_users}</div></div>
    <div class="kpi-card"><div class="label">원천 리뷰통계</div><div class="value blue">${fmtCount(d.source_review_stats_products || 0)}</div></div>
    <div class="kpi-card"><div class="label">원천 평점 커버</div><div class="value green">${fmtCount(d.source_avg_rating_products || 0)}</div></div>
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

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function displayText(value, fallback = '-') {
  if (value === null || value === undefined || value === '') return escapeHtml(fallback);
  return escapeHtml(value);
}

function jsonHtml(value) {
  return escapeHtml(JSON.stringify(value, null, 2));
}

function jsStringArg(value) {
  return escapeHtml(JSON.stringify(String(value ?? '')));
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
    <tr class="clickable" onclick="showReviewDetail(${jsStringArg(r.review_id)})">
      <td style="font-size:11px">${displayText(String(r.review_id || '').substring(0,30))}...</td>
      <td><span class="chip ${r.match_status === 'QUARANTINE' ? 'neg' : 'pos'}">${displayText(r.match_status)}</span></td>
      <td>${displayText(r.matched_product_id)}</td>
      <td>${fmtCount(r.entity_count)}</td><td>${fmtCount(r.fact_count)}</td><td>${fmtCount(r.signal_count)}</td>
      <td>${fmtCount(r.quarantine_count)}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function showReviewDetail(id) {
  const res = await fetch(API + '/api/reviews/' + encodeURIComponent(id));
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  dp.innerHTML = `<h3>리뷰 상세: ${displayText(id.substring(0,40))}...</h3><pre>${jsonHtml(d)}</pre>`;
}

async function loadProducts() {
  const res = await fetch(API + '/api/products');
  const data = await res.json();
  const panel = document.getElementById('explorerContent');
  panel.innerHTML = `<table><thead><tr>
    <th>상품명</th><th>브랜드</th><th>카테고리</th><th>원천 6M 리뷰</th><th>원천 평점</th><th>그래프 근거</th><th>Top BEE</th>
  </tr></thead><tbody>${data.items.map(p => `
    <tr class="clickable" onclick="showProductDetail(${jsStringArg(p.product_id)})">
      <td>${displayText(productDisplayName(p))}</td><td>${displayText(p.brand_name)}</td><td>${displayText(p.category_name)}</td>
      <td>${fmtCount(p.source_review_count_6m)}</td>
      <td>${fmtRating(p.source_avg_rating_6m)}</td>
      <td>${fmtCount(p.review_count_all || 0)}</td>
      <td>${displayText((p.top_bee_attr_ids||[]).slice(0,2).map(a => a.id ? a.id.split(':').pop() : '').join(', '))}</td>
    </tr>`).join('')}</tbody></table>`;
}

// Phase 8 G3: latest opened detail id — guards the async similar-products
// fetch against racing a newer detail render (stale section insertion).
let currentDetailId = null;

async function showProductDetail(id) {
  currentDetailId = id;
  const res = await fetch(API + '/api/products/' + encodeURIComponent(id));
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  const sp = d.serving_profile || {};
  const name = sp.representative_product_name ? `${sp.brand_name || ''} ${sp.representative_product_name}` : id;
  const summary = d.review_summary;
  const summaryText = summary ? (summary.short_summary || summary.long_summary || '-') : '-';
  dp.innerHTML = `
    <h3>상품 상세: ${displayText(name)}</h3>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="label">원천 6M 리뷰</div><div class="value blue">${fmtCount(sp.source_review_count_6m)}</div></div>
      <div class="kpi-card"><div class="label">원천 6M 평점</div><div class="value green">${fmtRating(sp.source_avg_rating_6m)}</div></div>
      <div class="kpi-card"><div class="label">그래프 근거 리뷰</div><div class="value">${fmtCount(sp.review_count_all || 0)}</div></div>
      <div class="kpi-card"><div class="label">리뷰 요약</div><div class="value" style="font-size:13px">${displayText(summaryStatusLabel(summary))}</div></div>
    </div>
    ${summary ? `<div class="panel" style="margin-top:12px">
      <h2>리뷰 요약</h2>
      <p>${displayText(summaryText)}</p>
    </div>` : ''}
    <pre>${jsonHtml(d)}</pre>
  `;
  // Phase 8 G3: attribute-similar products ("비슷한 상품") widget. Rendered after
  // the detail (fetched separately); an empty result hides the section entirely.
  renderSimilarProducts(id);
}

async function renderSimilarProducts(id) {
  let items = [];
  try {
    const res = await fetch(API + '/api/products/' + encodeURIComponent(id) + '/similar');
    if (!res.ok) return;  // 404 / error → no section
    items = ((await res.json()) || {}).items || [];
  } catch (e) { return; }
  if (!items.length) return;  // empty array → section not shown (per G3 UX contract)
  if (currentDetailId !== id) return;  // a newer detail replaced this one meanwhile
  const dp = document.getElementById('explorerDetail');
  if (!dp) return;
  const section = document.createElement('div');
  section.className = 'panel';
  section.style.marginTop = '12px';
  section.innerHTML = `<h2>비슷한 상품 <span style="font-weight:normal;font-size:12px;color:var(--text2)">공유 속성 기반 (${items.length})</span></h2>`
    + items.map(it => {
      const axes = (it.shared_axes || []).map(a =>
        `<span class="chip bee" title="IDF ${fmtRating(a.idf)}">${displayText(a.label)}</span>`
      ).join('');
      return `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
        <div><strong>${displayText(it.neighbor_name || it.product_id)}</strong>
        <span style="color:var(--text2);font-size:12px;margin-left:6px">score ${fmtScore(it.score)}</span></div>
        <div style="margin-top:4px">${axes || '<span style="color:var(--text2);font-size:11px">공유 근거 없음</span>'}</div>
      </div>`;
    }).join('');
  const pre = dp.querySelector('pre');
  if (pre) dp.insertBefore(section, pre); else dp.appendChild(section);
}

async function loadUsers() {
  const res = await fetch(API + '/api/users');
  const data = await res.json();
  const panel = document.getElementById('explorerContent');
  panel.innerHTML = `<table><thead><tr>
    <th>User ID</th><th>연령대</th><th>성별</th><th>피부타입</th><th>고민</th><th>선호 브랜드</th><th>스코프 선호</th>
  </tr></thead><tbody>${data.items.map(u => `
    <tr class="clickable" onclick="showUserDetail(${jsStringArg(u.user_id)})">
      <td>${displayText(u.user_id)}</td><td>${displayText(u.age_band)}</td><td>${displayText(u.gender)}</td>
      <td>${displayText(u.skin_type)}</td>
      <td>${displayText((u.concern_ids||[]).slice(0,2).map(c => c.id ? c.id.split(':').pop() : '').join(', '))}</td>
      <td>${displayText((u.preferred_brand_ids||[]).slice(0,2).map(b => b.id ? b.id.split(':').pop() : '').join(', '))}</td>
      <td>${displayText(scopeSummary(u))}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function showUserDetail(id) {
  const res = await fetch(API + '/api/users/' + id);
  const d = await res.json();
  const dp = document.getElementById('explorerDetail');
  dp.style.display = 'block';
  dp.innerHTML = `<h3>유저 상세: ${displayText(id)}</h3><pre>${jsonHtml(d)}</pre>`;
}

function scopeSummary(u) {
  const counts = {};
  (u.scoped_preference_ids || []).forEach(item => {
    const scope = item.scope_group || 'global';
    counts[scope] = (counts[scope] || 0) + 1;
  });
  return Object.entries(counts).map(([scope, count]) => `${scope}:${count}`).join(', ');
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

function fmtCount(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('ko-KR');
}

function fmtRating(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(2);
}

function fmtScore(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(4);
}

function summaryStatusLabel(summary) {
  if (!summary) return '요약 없음';
  if (summary.match_status === 'exact_category') return '요약 있음';
  return summary.match_status || '요약 있음';
}

// =============================================================================
// Recommendation Tester
// =============================================================================
const DEFAULT_WEIGHTS = {
  keyword_match: 0.16, residual_bee_attr_match: 0.07, context_match: 0.08,
  review_graph_weak_relation_match: 0.02,
  catalog_keyword_match: 0.04,
  concern_fit: 0.08, concern_bridge_fit: 0.04,
  ingredient_match: 0.07, brand_match_conf_weighted: 0.06,
  goal_fit_master: 0.05,
  category_affinity: 0.05, active_category_affinity: 0.02, freshness_boost: 0.04,
  source_popularity_score: 0.03, source_rating_score: 0.02,
  skin_type_fit: 0.06, purchase_loyalty_score: 0.04, novelty_bonus: 0.02,
  exact_owned_penalty: 0.05, owned_family_penalty: 0.03,
  same_family_explore_bonus: 0.02, repurchase_family_affinity: 0.03,
  repurchase_category_affinity: 0.03,
  tool_alignment: 0.02, coused_product_bonus: 0.02,
};
const WEIGHT_META = {
  keyword_match:              { label: '키워드 일치',      group: 'core',    desc: '리뷰에서 추출된 구체 키워드(촉촉, 물광 등)와 유저 선호 키워드의 겹침. 가장 핵심적인 매칭 축.' },
  residual_bee_attr_match:    { label: '잔여 BEE속성',    group: 'core',    desc: '키워드로 이미 커버되지 않은 상위 BEE 속성(제형, 발림성 등)의 추가 매칭. 키워드와 중복 카운트를 방지하는 잔여분만 반영.' },
  review_graph_weak_relation_match: { label: '약한 리뷰관계', group: 'core', desc: '승격되지 않은 long-tail 리뷰 신호가 유저 선호와 의미적으로 맞을 때만 낮은 가중치로 반영.' },
  context_match:              { label: '맥락 일치',       group: 'core',    desc: '사용 맥락(아침, 세안 후, 여름 등)이 유저 선호 맥락과 겹치는 정도.' },
  catalog_keyword_match:      { label: '상품명/카테고리', group: 'core',    desc: '유저 키워드가 상품마스터의 제품명/카테고리명에 직접 포함될 때 반영. 리뷰그래프 키워드와 분리된 상품마스터 truth 신호.' },
  concern_fit:                { label: '고민 적합도',      group: 'core',    desc: '유저 피부 고민(건조, 모공, 트러블 등)을 제품 리뷰 시그널이 얼마나 다루는지.' },
  concern_bridge_fit:         { label: '고민 브릿지',      group: 'core',    desc: '직접 고민 시그널이 없어도 BEE 속성에서 고민 대응 가능성을 추정한 간접 매칭.' },
  ingredient_match:           { label: '성분 일치',       group: 'core',    desc: '유저가 선호하는 성분(히알루론산, 나이아신아마이드 등)이 제품에 포함된 정도.' },
  brand_match_conf_weighted:  { label: '브랜드 신뢰',      group: 'core',    desc: '유저 선호 브랜드와 제품 브랜드 일치 여부. 구매 이력 기반이면 더 강한 신뢰도 반영.' },
  goal_fit_master:            { label: '목표(제품truth)',  group: 'core',    desc: '제품 마스터 데이터에 등록된 주요 효능(보습, 미백 등)과 유저 케어 목표의 일치.' },
  category_affinity:          { label: '명시 카테고리',    group: 'core',    desc: '명시 카테고리 선호와 제품 카테고리 일치. 활동 카테고리는 여기 포함하지 않음.' },
  active_category_affinity:   { label: '활동 카테고리',    group: 'meta',    desc: '구매/활동 카테고리 컨텍스트와 제품 카테고리 일치. 약한 보조 신호이며 단독 추천 근거가 아님.' },
  freshness_boost:            { label: '최신성',          group: 'meta',    desc: '최근 30일 리뷰 수 기반. 리뷰가 활발한 제품에 가산점. (10건↑=1.0, 3건↑=0.6, 1건↑=0.3)' },
  source_popularity_score:    { label: '원천 리뷰량',      group: 'meta',    desc: 'Snowflake 원천 기준 최근 6개월 리뷰 수를 낮은 가중치로 반영. 그래프 표본이 작은 제품의 외부 검증 신호.' },
  source_rating_score:        { label: '원천 평점',        group: 'meta',    desc: 'Snowflake 원천 기준 최근 6개월 평균 평점. 4.0점 이하는 가산하지 않고 4.0~5.0 구간만 완만하게 반영.' },
  skin_type_fit:              { label: '피부타입 적합',    group: 'meta',    desc: '유저 피부타입(건성/지성/복합/민감)과 제품 리뷰의 고민 시그널 간 궁합. 건성에 보습 긍정 → 가산, 끈적 부정 → 감점.' },
  purchase_loyalty_score:     { label: '구매 충성도',      group: 'personal', desc: '유저가 해당 브랜드 제품을 재구매한 이력이 있으면 1.0, 최근 구매면 0.5.' },
  novelty_bonus:              { label: '신규성 보너스',    group: 'personal', desc: '유저가 아직 모르는 브랜드/제품일수록 높은 점수. 이미 보유한 제품=0, 같은 패밀리=0.2, 아는 브랜드=0.5, 처음=1.0.' },
  exact_owned_penalty:        { label: '동일SKU 감점',     group: 'personal', desc: '유저가 이미 보유한 동일 SKU를 강하게 감점. 반복 추천 방지.' },
  owned_family_penalty:       { label: '보유패밀리 감점',  group: 'personal', desc: '유저가 이미 같은 variant family(같은 제품군의 다른 호수/용량) 제품을 보유하면 감점. 중복 추천 방지.' },
  same_family_explore_bonus:  { label: '패밀리탐색 가산',  group: 'personal', desc: '같은 제품군의 다른 옵션을 탐색할 때 소폭 가산. 익숙한 라인 내 확장 추천.' },
  repurchase_family_affinity: { label: '재구매패밀리 가산', group: 'personal', desc: '유저가 재구매한 패밀리의 다른 SKU에 가산. "이 라인 좋아하시네요" 식의 확장 추천.' },
  repurchase_category_affinity: { label: '재구매카테고리', group: 'personal', desc: '반복 구매한 카테고리 값이 제품명/카테고리명에 직접 닿을 때 가산. 상품마스터 taxonomy 기반 구매행동 신호.' },
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
  explore: 'Evidence-qualified 탐색 모드. 상품마스터 truth, 리뷰 relation, 구매 행동 중 하나 이상이 유저와 맞아야 노출.',
  strict: '선호 카테고리 불일치 상품을 제외하고, evidence-qualified 후보만 추천.',
  compare: '비교/대안 확인용. 카테고리 폭은 넓히되 evidence-qualified 후보만 정상 추천으로 노출.',
};
const RECOMMEND_CATEGORY_TABS = [
  { group: 'all', label: '전체', count: 0 },
  { group: 'skincare', label: '스킨케어', count: 0 },
  { group: 'makeup', label: '메이크업', count: 0 },
  { group: 'bodycare', label: '바디', count: 0 },
  { group: 'haircare', label: '헤어', count: 0 },
  { group: 'fragrance', label: '향수', count: 0 },
  { group: 'other', label: '기타', count: 0 },
];
let recommendCategoryTabs = RECOMMEND_CATEGORY_TABS;
let activeRecommendCategory = 'all';
let recommendHasRun = false;
// Last rendered recommend results + the user they were computed for. The
// inline "why this" subgraph (toggleRecGraph) reads these instead of the
// response being re-embedded in the DOM, and instead of re-fetching -- the
// subgraph is built purely from explanation_paths already in the response
// (server does NOT expose a recompute endpoint; recomputing would drift from
// what the user saw once presets/query injection land).
let lastRecommendResults = [];
let lastRecommendUserId = '';

async function initRecommendPanel() {
  // Non-login option (Phase 6 Track B3): lets askQuery submit as an anonymous
  // search (user_id null, resolved_mode="search") without picking a user.
  // Set before the /api/users fetch so it's present even if that call fails.
  const sel = document.getElementById('recUser');
  sel.innerHTML = '<option value="">로그인 없이 (검색만)</option>';
  try {
    const users = await fetch(API + '/api/users').then(r => r.json());
    sel.innerHTML += users.items.map(u => (
      `<option value="${displayText(u.user_id)}">${displayText(u.user_id)} (${displayText(u.skin_type, '')}/${displayText(u.gender, '')})</option>`
    )).join('');
  } catch(e) {}
  await loadRecommendCategories();
  await loadRecommendPresets();
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
      <div class="weight-group-label">${displayText(GROUP_LABELS[g] || g)}</div>
      ${items.map(({ key, value, meta }) => `
        <div class="slider-group" title="${displayText(meta.desc, '')}">
          <label>
            <span class="weight-name">${displayText(meta.label || key)}</span>
            <span id="w_${key}_val">${value.toFixed(2)}</span>
          </label>
          <input type="range" min="0" max="50" value="${Math.round(value*100)}" id="w_${key}"
            oninput="document.getElementById('w_${key}_val').textContent=(this.value/100).toFixed(2); showWeightDesc('${key}')">
          <div class="weight-desc" id="desc_${key}" style="display:none">${displayText(meta.desc, '')}</div>
        </div>
      `).join('')}
    </div>
  `).join('');
}

async function loadRecommendCategories() {
  try {
    const payload = await fetch(API + '/api/recommend/categories').then(r => r.json());
    if (Array.isArray(payload.items) && payload.items.length) {
      recommendCategoryTabs = payload.items;
    }
  } catch(e) {
    recommendCategoryTabs = RECOMMEND_CATEGORY_TABS;
  }
  renderRecommendCategoryTabs();
}

function renderRecommendCategoryTabs() {
  const container = document.getElementById('recCategoryTabs');
  if (!container) return;
  container.innerHTML = recommendCategoryTabs.map(tab => {
    const active = tab.group === activeRecommendCategory ? ' active' : '';
    const count = Number(tab.count);
    const countText = Number.isNaN(count) ? '-' : count.toLocaleString('ko-KR');
    return `
      <button class="category-tab${active}" type="button" onclick="setRecommendCategory(${jsStringArg(tab.group)})">
        ${displayText(tab.label || tab.group)}
        <span class="tab-count">${displayText(countText)}</span>
      </button>
    `;
  }).join('');
}

function setRecommendCategory(group) {
  activeRecommendCategory = group || 'all';
  renderRecommendCategoryTabs();
  if (recommendHasRun) runRecommend();
}

// =============================================================================
// Recommend intent presets (Phase 6 Track A1 frontend)
// =============================================================================
// GET /api/recommend/presets is the single source of truth for preset
// key/label/description -- no preset copy is hardcoded here. User mode (dev
// mode OFF) shows these cards instead of the raw weight/mode/shrinkage
// controls; runRecommend() sends only `preset` in that case (see below).
let recommendPresets = [];
let selectedPresetKey = 'balanced';

async function loadRecommendPresets() {
  try {
    const payload = await fetch(API + '/api/recommend/presets').then(r => r.json());
    recommendPresets = Array.isArray(payload.items) ? payload.items : [];
  } catch(e) {
    recommendPresets = [];
  }
  if (recommendPresets.length && !recommendPresets.some(p => p.key === selectedPresetKey)) {
    selectedPresetKey = recommendPresets[0].key;
  }
  renderPresetCards();
}

function renderPresetCards() {
  const container = document.getElementById('presetCards');
  if (!container) return;
  if (!recommendPresets.length) {
    container.innerHTML = '<div class="empty">프리셋을 불러올 수 없습니다</div>';
    return;
  }
  container.innerHTML = recommendPresets.map(p => {
    const checked = p.key === selectedPresetKey;
    return `
      <label class="preset-card${checked ? ' active' : ''}">
        <input type="radio" name="recPreset" value="${displayText(p.key)}" ${checked ? 'checked' : ''}
          onchange="selectPreset(${jsStringArg(p.key)})">
        <div class="preset-card-body">
          <div class="preset-card-label">${displayText(p.label_ko || p.key)}</div>
          <div class="preset-card-desc">${displayText(p.description_ko, '')}</div>
        </div>
      </label>
    `;
  }).join('');
}

function selectPreset(key) {
  selectedPresetKey = key;
  renderPresetCards();
  if (recommendHasRun) runRecommend();
}

function showWeightDesc(key) {
  const el = document.getElementById('desc_' + key);
  if (el) el.style.display = 'block';
}

async function runRecommend() {
  const userId = document.getElementById('recUser').value;
  if (!userId) return;
  // Clear any stale ask-interpretation chips from a previous /api/ask call --
  // a preset re-run otherwise leaves them floating over the new results (G1).
  clearAskInterpretation();

  let body;
  if (isDevMode()) {
    // Developer mode: unchanged behavior -- send slider/mode values as
    // before. The server now also respects a shrinkage_k-only change (no
    // weights touched), which it previously discarded silently.
    const weights = {};
    let customized = false;
    for (const k of Object.keys(DEFAULT_WEIGHTS)) {
      const el = document.getElementById('w_' + k);
      if (!el) continue;
      const val = parseInt(el.value) / 100;
      weights[k] = val;
      if (Math.abs(val - DEFAULT_WEIGHTS[k]) > 0.005) customized = true;
    }
    body = {
      user_id: userId,
      mode: document.getElementById('recMode').value,
      category_group: activeRecommendCategory,
      top_k: 10,
      weights: customized ? weights : null,  // null → server uses YAML config
      shrinkage_k: parseFloat(document.getElementById('shrinkageK').value),
      diversity_weight: parseInt(document.getElementById('diversityW').value) / 100,
    };
  } else {
    // User mode: send only the selected intent preset -- no weights/
    // shrinkage/diversity/mode. The server resolves all of those from
    // configs/recommend_presets.yaml (see /api/recommend `preset` handling).
    body = {
      user_id: userId,
      category_group: activeRecommendCategory,
      top_k: 10,
      preset: selectedPresetKey,
    };
  }

  const res = await fetch(API + '/api/recommend', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const data = await res.json();
  recommendHasRun = true;
  renderRecommendResults(data);
}

function renderPathSnippets(snippets) {
  if (!Array.isArray(snippets) || !snippets.length) return '';
  return `
    <details class="snippet-details">
      <summary>리뷰 근거 ${snippets.length}건</summary>
      <div class="snippet-list">
        ${snippets.map(s => `
          <div class="snippet-item">
            <p class="snippet-text">${displayText(s.text)}</p>
            ${s.review_id ? `<span class="snippet-review-id">review_id: ${displayText(s.review_id)}</span>` : ''}
          </div>
        `).join('')}
      </div>
    </details>
  `;
}

// Tear down any inline subgraphs from the previous render before #recResults'
// cards are replaced, so their cytoscape instances are released (no leak).
// Shared by both result renderers below -- runAsk() (Phase 6 Track B3) can
// route to either renderRecommendResults or renderSearchResults depending on
// resolved_mode, and either may follow the other on the same #recResults.
function destroyInlineRecGraphs() {
  document.querySelectorAll('#recResults .rec-graph-inline').forEach(c => GraphView.destroy(c));
}

// Developer-mode summary of preset_used/weights_used (Phase 6 closing review
// G3). Rendered unconditionally into #recMeta -- like the rec-card dev-only
// blocks above, visibility is pure CSS (.dev-only, see app.css) so no
// re-render is needed when the developer-mode toggle flips. Works for both
// /api/recommend (always has weights_used) and /api/ask's recommend-mode
// envelope (may omit weights_used): each part renders only if its field is
// present, and the whole block is omitted if neither is.
function renderDevMetaBlock(data) {
  const parts = [];
  const preset = data.preset_used;
  if (preset && typeof preset === 'object') {
    const overrideCount = preset.weight_overrides ? Object.keys(preset.weight_overrides).length : 0;
    parts.push(`
      <div class="dev-meta-row">
        <span class="label">preset_used:</span>
        ${displayText(preset.key)} (${displayText(preset.label_ko, preset.key)}) ·
        mode: ${displayText(preset.mode)} ·
        shrinkage_k: ${displayText(preset.shrinkage_k)} ·
        diversity_weight: ${displayText(preset.diversity_weight)} ·
        override ${fmtCount(overrideCount)}개
      </div>
    `);
  }
  const weights = data.weights_used;
  if (weights && typeof weights === 'object') {
    const keys = Object.keys(weights);
    const diffCount = keys.filter(k => !(k in DEFAULT_WEIGHTS) || Math.abs(Number(weights[k]) - DEFAULT_WEIGHTS[k]) > 0.0001).length;
    parts.push(`
      <details class="dev-meta-row">
        <summary>weights_used: ${keys.length}개 (기본과 다른 항목: ${diffCount})</summary>
        <pre>${jsonHtml(weights)}</pre>
      </details>
    `);
  }
  if (!parts.length) return '';
  return `<div class="dev-only dev-meta-block">${parts.join('')}</div>`;
}

// Phase 8 G5: "관련 상품 더보기" — 2차 관련 상품 섹션. 서버가 1차 결과(검색/추천)
// 상위 앵커의 ungated 속성-공유 이웃을 related_products로 실어 보내면, 1차 결과
// 카드 뒤에 덧붙인다. 이웃명 + score + anchor 귀속 문구 + 공유 축 라벨 칩(P8-2
// 칩 스타일 재사용), 클릭 → 상품 상세. related_products가 비면 섹션 자체를
// 미삽입하며, 1차 결과 렌더 코드는 건드리지 않는다(반환 HTML을 뒤에 이어붙일 뿐).
function renderRelatedProducts(data) {
  const related = (data && data.related_products) || [];
  if (!related.length) return '';
  const rows = related.map(it => {
    const axes = (it.shared_axes || []).map(a =>
      `<span class="chip bee" title="IDF ${fmtRating(a.idf)}">${displayText(a.label)}</span>`
    ).join('');
    const anchorName = it.anchor_name || it.anchor_product_id || '';
    return `
      <div class="related-item" onclick="showProductDetail(${jsStringArg(it.product_id)})"
           style="padding:8px 0;border-bottom:1px solid var(--border);cursor:pointer">
        <div><strong>${displayText(it.neighbor_name || it.product_id)}</strong>
          <span style="color:var(--text2);font-size:12px;margin-left:6px">score ${fmtScore(it.score)}</span></div>
        <div style="color:var(--text2);font-size:12px;margin-top:2px">'${displayText(anchorName)}'과 속성 공유</div>
        <div style="margin-top:4px">${axes || '<span style="color:var(--text2);font-size:11px">공유 근거 없음</span>'}</div>
      </div>`;
  }).join('');
  return `<div class="panel related-products" style="margin-top:12px">
    <h2>관련 상품 더보기 <span style="font-weight:normal;font-size:12px;color:var(--text2)">공유 속성 기반 (${related.length})</span></h2>
    ${rows}
  </div>`;
}

function renderRecommendResults(data) {
  destroyInlineRecGraphs();

  const results = data.results || [];
  lastRecommendResults = results;
  lastRecommendUserId = data.user_id || document.getElementById('recUser').value || '';
  const categoryLabel = data.category_label || (
    recommendCategoryTabs.find(tab => tab.group === activeRecommendCategory)?.label || activeRecommendCategory
  );
  document.getElementById('recMeta').innerHTML = `
    <div class="kpi-grid">
      <div class="kpi-card"><div class="label">카테고리</div><div class="value" style="font-size:18px">${displayText(categoryLabel)}</div></div>
      <div class="kpi-card"><div class="label">탭 후보군</div><div class="value blue">${fmtCount(data.category_filtered_count)}</div></div>
      <div class="kpi-card"><div class="label">후보군</div><div class="value blue">${fmtCount(data.candidate_count)}</div></div>
      <div class="kpi-card"><div class="label">최종 결과</div><div class="value green">${results.length}</div></div>
      <div class="kpi-card"><div class="label">다음 질문</div><div class="value" style="font-size:13px">${data.next_question ? displayText(data.next_question.question) : '-'}</div></div>
    </div>
    ${renderDevMetaBlock(data)}`;

  const container = document.getElementById('recResults');
  if (!results.length) {
    container.innerHTML = '<div class="empty">추천 결과 없음 (유저와 정렬된 상품마스터/리뷰그래프/구매 evidence 부족)</div>';
    return;
  }
  container.innerHTML = results.map((r, idx) => {
    const product = r.product || {};
    const sourceTrust = r.source_trust || {};
    const eligibility = r.eligibility || {};
    const evidenceFamilies = eligibility.evidence_families || [];
    const scoreLayers = r.score_layers || {};
    const paths = r.explanation_paths || [];
    const hooks = r.hooks || {};
    const overlap = r.overlap_concepts || [];
    const productName = product.representative_product_name
      ? [product.brand_name, product.representative_product_name].filter(Boolean).join(' ')
      : (r.product_id || product.product_id || '-');
    const finalScore = fmtScore(r.final_score);
    const rankScore = fmtScore(r.rank_score);
    const rawScore = fmtScore(r.raw_score);
    const shrinkedScore = fmtScore(r.shrinked_score);
    const diversity = Number(r.diversity_bonus);
    const diversityText = Number.isNaN(diversity)
      ? '-'
      : `${diversity >= 0 ? '+' : ''}${diversity.toFixed(4)}`;
    return `
      <div class="rec-card">
        <div class="rank">#${r.rank || '-'}</div>
        <div>
          <strong>${displayText(productName)}</strong>
          <span class="score">추천점수: ${finalScore} / 정렬점수: ${rankScore}</span>
          <div class="score-debug dev-only">raw: ${rawScore} · shrink: ${shrinkedScore} · diversity: ${diversityText}</div>
          <div class="explanation">${displayText(r.explanation, '설명 없음')}</div>
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">Evidence:</span> ${evidenceFamilies.length ? evidenceFamilies.map(f => `<span class="chip rel">${displayText(f)}</span>`).join(' ') : '없음'}</span>
          </div>
          <div class="hooks dev-only" style="margin-top:8px">
            <span><span class="label">상품마스터:</span> ${fmtScore(scoreLayers.master_truth_score)}</span>
            <span><span class="label">리뷰그래프:</span> ${fmtScore(scoreLayers.review_graph_score)}</span>
            <span><span class="label">프로필:</span> ${fmtScore(scoreLayers.profile_fit_score)}</span>
            <span><span class="label">리뷰활동:</span> ${fmtScore(scoreLayers.product_activity_score)}</span>
            <span><span class="label">구매행동:</span> ${fmtScore(scoreLayers.purchase_behavior_score)}</span>
            <span><span class="label">Source trust:</span> ${fmtScore(scoreLayers.source_trust_score)}</span>
          </div>
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">원천 6M 리뷰:</span> ${fmtCount(sourceTrust.review_count_6m)}</span>
            <span><span class="label">원천 평점:</span> ${fmtRating(sourceTrust.avg_rating_6m)}</span>
            <span><span class="label">요약:</span> ${displayText(summaryStatusLabel(r.review_summary))}</span>
          </div>
          ${paths.length ? '<h3 style="margin-top:8px">설명 경로</h3>' + paths.map(p => {
            const contribution = Number(p.contribution);
            const contributionText = Number.isNaN(contribution)
              ? '-'
              : `${contribution >= 0 ? '+' : ''}${contribution.toFixed(3)}`;
            const concept = p.id ? p.id.split(':').pop() : '-';
            return `
              <div class="path-row">
                <span class="chip ner">${displayText(p.user_edge)}</span>
                <span class="arrow">→</span>
                <span class="chip bee">${displayText(concept)}</span>
                <span class="arrow">→</span>
                <span class="chip rel">${displayText(p.product_edge)}</span>
                <span style="margin-left:8px;color:${contribution < 0 ? 'var(--red)' : 'var(--green)'}">(${contributionText})</span>
              </div>
              ${renderPathSnippets(p.snippets)}
            `;
          }).join('') : ''}
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">🔍 탐색:</span> ${displayText(hooks.discovery)}</span>
            <span><span class="label">🤔 고려:</span> ${displayText(hooks.consideration)}</span>
            <span><span class="label">🎯 전환:</span> ${displayText(hooks.conversion)}</span>
          </div>
          ${overlap.length ? `<div style="margin-top:8px">${overlap.map(c => `<span class="chip bee">${displayText(c)}</span>`).join('')}</div>` : ''}
          <div class="rec-graph-actions">
            <button class="btn btn-sm rec-graph-toggle" type="button" id="rec-graph-btn-${idx}" onclick="toggleRecGraph(${idx})">🕸 그래프</button>
          </div>
          <div class="rec-graph-inline" id="rec-graph-${idx}" style="display:none"></div>
        </div>
      </div>
    `;
  }).join('') + renderRelatedProducts(data);
}

// =============================================================================
// Recommendation inline "why this" subgraph (Phase 6 Track A3)
// =============================================================================
// Per-card cytoscape mini-view built entirely from the result's
// explanation_paths (user node + product node + one concept node per path).
// Independent per card thanks to GraphView's container-keyed instances.

// explanation_paths[].type uses the explainer's concept-type vocabulary, which
// is finer-grained than the /api/graphs/* node types that TYPE_COLORS keys off
// of. Collapse each concept type onto the matching color key so the subgraph
// shares the Graph Viewer's color language; unknown types fall through to the
// raw type (GraphView greys anything not in TYPE_COLORS).
const CONCEPT_TYPE_TO_NODE_TYPE = {
  keyword: 'keyword', semantic_keyword: 'keyword', weak_semantic_keyword: 'keyword', catalog_keyword: 'keyword',
  bee_attr: 'bee_attr', semantic_bee_attr: 'bee_attr', weak_semantic_bee_attr: 'bee_attr',
  concern: 'concern', concern_bridge: 'concern',
  context: 'context',
  brand: 'brand', repurchase_brand: 'brand', recent_purchase_brand: 'brand',
  category: 'category', active_category: 'category', repurchase_category: 'category',
  goal_master: 'goal',
  ingredient: 'ingredient',
  tool: 'tool',
  coused: 'coused',
};
const REC_GRAPH_NEG_COLOR = '#ef4444';  // negative-contribution edges (matches --red)

function buildExplanationSubgraph(result, userId) {
  const paths = Array.isArray(result.explanation_paths) ? result.explanation_paths : [];
  const product = result.product || {};
  const productId = result.product_id || product.product_id || 'product';
  const productName = product.representative_product_name
    ? [product.brand_name, product.representative_product_name].filter(Boolean).join(' ')
    : String(productId);
  const userNodeId = 'user:' + userId;
  const productNodeId = 'product:' + productId;

  // Relative edge width: |contribution| scaled to 1px..5px against the card's
  // own largest |contribution| (each card scales independently).
  const maxAbs = paths.reduce((m, p) => Math.max(m, Math.abs(Number(p.contribution) || 0)), 0);
  const widthFor = (c) => {
    const a = Math.abs(Number(c) || 0);
    return maxAbs > 0 ? 1 + (a / maxAbs) * 4 : 1;
  };

  // Merge duplicate concept nodes (same concept reached by >1 path) but keep
  // one edge pair per path; accumulate snippet counts across merged paths.
  const conceptNodes = new Map();  // node id -> {base, type, snippetCount}
  const edges = [];
  paths.forEach((p, idx) => {
    const rawType = p.type || 'concept';
    const nodeType = CONCEPT_TYPE_TO_NODE_TYPE[rawType] || rawType;
    const seg = p.id ? String(p.id).split(':').pop() : rawType;
    const conceptId = 'c:' + rawType + ':' + (p.id || idx);
    const snips = Array.isArray(p.snippets) ? p.snippets.length : 0;
    const existing = conceptNodes.get(conceptId);
    if (existing) {
      existing.snippetCount += snips;
    } else {
      conceptNodes.set(conceptId, { base: seg, type: nodeType, snippetCount: snips });
    }
    const contribution = Number(p.contribution) || 0;
    const width = widthFor(contribution);
    const color = contribution < 0 ? REC_GRAPH_NEG_COLOR : null;
    edges.push({ source: userNodeId, target: conceptId, label: p.user_edge || '', width, color });
    edges.push({ source: conceptId, target: productNodeId, label: p.product_edge || '', width, color });
  });

  const nodes = [
    { id: userNodeId, label: String(userId || '유저'), type: 'user', main: true },
    { id: productNodeId, label: productName, type: 'product', main: true },
  ];
  conceptNodes.forEach((v, id) => {
    const label = v.snippetCount > 0 ? `${v.base} 💬${v.snippetCount}` : v.base;
    nodes.push({ id, label, type: v.type });
  });
  return { nodes, edges };
}

function toggleRecGraph(idx) {
  const container = document.getElementById('rec-graph-' + idx);
  if (!container) return;
  const btn = document.getElementById('rec-graph-btn-' + idx);
  const isOpen = container.dataset.open === '1';
  if (isOpen) {
    GraphView.destroy(container);
    container.innerHTML = '';
    container.style.display = 'none';
    container.dataset.open = '0';
    if (btn) btn.classList.remove('active');
    return;
  }
  const result = lastRecommendResults[idx];
  if (!result) return;
  container.style.display = 'block';
  container.dataset.open = '1';
  if (btn) btn.classList.add('active');
  const paths = result.explanation_paths || [];
  if (!paths.length) {
    // Empty state only -- do not spin up cytoscape for nothing to draw.
    container.innerHTML = '<div class="empty">설명 경로가 없는 추천입니다</div>';
    return;
  }
  container.innerHTML = '';
  GraphView.render(container, buildExplanationSubgraph(result, lastRecommendUserId), {
    layout: { padding: 24, nodeRepulsion: 6000, idealEdgeLength: 90 },
  });
}

// =============================================================================
// Integrated query bar: POST /api/ask (Phase 6 Track B3)
// =============================================================================
// One input serves both intents -- the server decides resolved_mode from
// whether user_id is present:
//   - user selected      -> resolved_mode="recommend": query concepts are
//     injected as a request-scoped preference on top of the existing
//     recommend path. Results are the exact /api/recommend result shape
//     (explanation_paths included), so renderRecommendResults() is reused
//     as-is -- no third card layout.
//   - "로그인 없이" (user_id null) -> resolved_mode="search": results are the
//     /api/search shape (no explanation_paths, hence no graph button here).
// Chips are display-only for this scope: there is no per-chip "remove and
// re-run"; editing the query text and resubmitting is the interaction.
const ASK_CHIP_FALLBACK_COLOR = '#6b7280';  // matches graph_view.js's DEFAULT_NODE_COLOR

// [F4-c''] Profile-reference class -> Korean label (server sends enum class names
// only; localization lives here, next to the other ask-UI labels). Violet chip
// color distinguishes "my profile reflected" chips from query-concept chips.
const PROFILE_REF_CHIP_COLOR = '#7c3aed';
const PROFILE_REF_CLASS_LABELS = {
  concerns: '고민',
  skin: '피부',
  goals: '목표',
  preferred_brands: '선호 브랜드',
  preferred_keywords: '취향',
  repurchase: '재구매',
  owned: '보유 제품',
};

function conceptIdSegment(id) {
  return String(id || '').split(':').pop();
}

function askConceptColor(conceptType) {
  const colors = window.GraphView && GraphView.TYPE_COLORS;
  return (colors && colors[conceptType]) || ASK_CHIP_FALLBACK_COLOR;
}

function clearAskInterpretation() {
  const el = document.getElementById('askInterpretation');
  if (el) el.innerHTML = '';
}

function renderAskError(message) {
  const el = document.getElementById('askInterpretation');
  if (!el) return;
  el.innerHTML = `<div class="ask-banner ask-banner-error">${displayText(message)}</div>`;
}

function renderAskInterpretation(data) {
  const el = document.getElementById('askInterpretation');
  if (!el) return;
  const interp = data.interpretation || {};
  const resolved = interp.resolved_concepts || [];
  const avoided = interp.avoided_ingredient_concept_ids || [];
  const unresolved = interp.unresolved_terms || [];
  // (interp.warnings || []) -- defensive fallback so this renders safely
  // whether or not the response already carries the warnings contract.
  const warnings = interp.warnings || [];

  // Resolved-concept labels are reused for both the chips and the F4-a summary line.
  const resolvedLabels = resolved.map(c => (c.label && String(c.label).trim()) || conceptIdSegment(c.concept_id));
  const chips = resolved.map((c, i) => {
    const color = askConceptColor(c.concept_type);
    return `<span class="chip ask-chip" style="background:${color}26;color:${color};border-color:${color}66" title="${displayText(c.concept_id)}">${displayText(resolvedLabels[i])}</span>`;
  }).concat(avoided.map(id => (
    `<span class="chip ask-chip ask-chip-avoid" title="${displayText(id)}">🚫 ${displayText(conceptIdSegment(id))}</span>`
  ))).concat(unresolved.map(term => (
    `<span class="chip ask-chip ask-chip-unresolved" title="아직 사전에 없는 표현이에요">${displayText(term)}</span>`
  )));

  // [F4-c''] "내 프로필 반영" chips from applied_profile_refs (recommend mode only —
  // absent for anonymous search, so no profile chips render there). Each chip:
  // "고민(진정·보습)".
  const appliedRefs = data.applied_profile_refs || [];
  const profileClassLabels = appliedRefs.map(r => PROFILE_REF_CLASS_LABELS[r.class] || r.class);
  const profileChips = appliedRefs.map(r => {
    const cls = displayText(PROFILE_REF_CLASS_LABELS[r.class] || r.class);
    const concepts = (r.concepts || []).map(displayText).join('·');
    const color = PROFILE_REF_CHIP_COLOR;
    return `<span class="chip ask-chip" style="background:${color}26;color:${color};border-color:${color}66">${concepts ? `${cls}(${concepts})` : cls}</span>`;
  });

  // [F4-a] One-line reflection summary above the chips.
  const resolvedSummary = [...new Set(resolvedLabels.map(displayText))].join('·');
  const profileSummary = [...new Set(profileClassLabels.map(displayText))].join('·');
  let summaryInner = '';
  if (resolvedSummary && profileSummary) {
    summaryInner = `이 결과: ${resolvedSummary} + 내 프로필 반영 ${profileSummary} 기반`;
  } else if (resolvedSummary) {
    summaryInner = `이 결과: ${resolvedSummary} 기반`;
  } else if (profileSummary) {
    summaryInner = `이 결과: 내 프로필 반영 ${profileSummary} 기반`;
  } else if (data.resolved_mode === 'recommend') {
    // Recommend mode always personalizes off stored prefs even with no query/profile hit.
    summaryInner = '이 결과: 개인화 기반';
  }
  const summary = summaryInner
    ? `<div class="ask-summary" style="margin:2px 0 8px;color:#6b7280;font-size:13px">${summaryInner}</div>`
    : '';

  const badge = interp.llm_used === false ? '<span class="ask-badge">사전 해석</span>' : '';
  const banner = data.relaxed ? '<div class="ask-banner ask-banner-info">조건에 꼭 맞는 상품이 적어 관련도순으로 보여드려요</div>' : '';
  const warningBanners = warnings.map(w => `<div class="ask-banner ask-banner-warn">${displayText(w)}</div>`).join('');

  const queryRow = (chips.length || badge)
    ? `<div class="ask-interpretation-row"><span class="ask-interpretation-label">질의 해석</span>${badge}${chips.join('')}</div>`
    : '';
  const profileRow = profileChips.length
    ? `<div class="ask-interpretation-row ask-profile-row"><span class="ask-interpretation-label">내 프로필 반영</span>${profileChips.join('')}</div>`
    : '';

  if (!queryRow && !profileRow && !banner && !warningBanners && !summary) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = `
    ${warningBanners}
    ${banner}
    ${summary}
    ${queryRow}
    ${profileRow}
  `;
}

async function runAsk() {
  const btn = document.getElementById('askBtn');
  if (btn && btn.disabled) return;  // already in flight -- guards a repeat Enter

  const queryEl = document.getElementById('askQuery');
  const query = (queryEl.value || '').trim();
  const userId = document.getElementById('recUser').value || null;

  if (btn) { btn.disabled = true; btn.textContent = '해석 중...'; }
  clearAskInterpretation();

  try {
    const res = await fetch(API + '/api/ask', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        user_id: userId,
        query,
        preset: isDevMode() ? null : selectedPresetKey,
        category_group: activeRecommendCategory,
        top_k: 10,
      }),
    });
    if (!res.ok) {
      let detail = '질의를 처리할 수 없습니다.';
      try {
        const errBody = await res.json();
        if (errBody && errBody.detail) detail = errBody.detail;
      } catch(e) {}
      renderAskError(detail);
      return;
    }
    const data = await res.json();
    recommendHasRun = true;
    renderAskInterpretation(data);
    if (data.resolved_mode === 'recommend') {
      renderRecommendResults(data);
    } else {
      renderSearchResults(data);
    }
  } catch(e) {
    renderAskError('질의 처리 중 오류가 발생했습니다: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '질문하기'; }
  }
}

// Simple search-mode result card: /api/search-shaped items (product,
// overlap_concepts, relevance_score, eligibility) with no explanation_paths,
// so no 🕸 그래프 button (there is no path to visualize).
function renderSearchResults(data) {
  destroyInlineRecGraphs();

  // /api/ask's search-mode envelope carries none of /api/recommend's meta
  // fields (candidate_count, next_question, ...) -- clear rather than show a
  // KPI grid full of dashes left over from a previous recommend run.
  const metaEl = document.getElementById('recMeta');
  if (metaEl) metaEl.innerHTML = '';

  const results = data.results || [];
  const container = document.getElementById('recResults');
  if (!container) return;
  if (!results.length) {
    container.innerHTML = '<div class="empty">검색 결과 없음 (개념이 해석되지 않았거나 겹치는 상품이 없습니다)</div>';
    return;
  }
  container.innerHTML = results.map((r, idx) => {
    const product = r.product || {};
    const evidenceFamilies = (r.eligibility || {}).evidence_families || [];
    const overlap = r.overlap_concepts || [];
    const productName = product.representative_product_name
      ? [product.brand_name, product.representative_product_name].filter(Boolean).join(' ')
      : (r.product_id || product.product_id || '-');
    return `
      <div class="rec-card">
        <div class="rank">#${idx + 1}</div>
        <div>
          <strong>${displayText(productName)}</strong>
          <span class="score">관련도: ${fmtScore(r.relevance_score)}</span>
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">브랜드:</span> ${displayText(product.brand_name)}</span>
            <span><span class="label">카테고리:</span> ${displayText(product.category_name)}</span>
            <span><span class="label">Evidence:</span> ${evidenceFamilies.length ? evidenceFamilies.map(f => `<span class="chip rel">${displayText(f)}</span>`).join(' ') : '없음'}</span>
          </div>
          ${overlap.length ? `<div style="margin-top:8px">${overlap.map(c => `<span class="chip bee">${displayText(c)}</span>`).join('')}</div>` : ''}
        </div>
      </div>
    `;
  }).join('') + renderRelatedProducts(data);
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
      + products.items.map(p => `<option value="product:${displayText(p.product_id)}">🏷️ ${displayText(productDisplayName(p))}</option>`).join('')
      + users.items.map(u => `<option value="user:${displayText(u.user_id)}">👤 ${displayText(u.user_id)}</option>`).join('');
  } catch(e) {}
}

async function loadGraph() {
  const target = document.getElementById('graphTarget').value;
  if (!target) return;
  const [type, id] = target.split(':');
  const view = document.getElementById('graphView').value || 'corpus';
  const viewParam = type === 'product' ? `?view=${view}` : '';
  const url = type === 'product' ? `/api/graphs/product/${id}${viewParam}` : `/api/graphs/user/${id}`;
  const data = await fetch(API + url).then(r => r.json());
  const info = document.getElementById('graphViewInfo');
  if (info && data.view_mode) {
    info.textContent = data.view_mode === 'corpus'
      ? `Corpus view: promoted 시그널만 표시 (nodes: ${data.nodes.length}, edges: ${data.edges.length})`
      : `Evidence view: 전체 시그널 표시 (nodes: ${data.nodes.length}, edges: ${data.edges.length})`;
  }
  GraphView.render(document.getElementById('graph-container'), data);
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
      <div class="kpi-card"><div class="label">${displayText(k.replace('quarantine_',''))}</div><div class="value yellow">${fmtCount(v)}</div></div>
    `).join('') || '<div class="kpi-card"><div class="label">격리 없음</div><div class="value green">0</div></div>';

    const list = document.getElementById('quarantineList');
    if (!entries.items.length) {
      list.innerHTML = '<div class="empty">격리 항목 없음</div>';
    } else {
      list.innerHTML = `<table><thead><tr><th>타입</th><th>사유</th><th>상태</th><th>상세</th></tr></thead>
        <tbody>${entries.items.map(e => `<tr>
          <td><span class="chip rel">${displayText((e.table||'').replace('quarantine_',''))}</span></td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${displayText(e.reason)}</td>
          <td>${displayText(e.status)}</td>
          <td><button class="btn btn-sm" onclick="alert(${jsStringArg(JSON.stringify(e, null, 2))})">JSON</button></td>
        </tr>`).join('')}</tbody></table>`;
    }
  } catch(e) {
    document.getElementById('quarantineKpi').innerHTML = '<div class="empty">데이터 로드 필요</div>';
  }
}

// =============================================================================
// Init
// =============================================================================
initDevMode();
loadDashboard();
