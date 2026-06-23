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

async function showProductDetail(id) {
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

async function initRecommendPanel() {
  try {
    const users = await fetch(API + '/api/users').then(r => r.json());
    const sel = document.getElementById('recUser');
    sel.innerHTML = users.items.map(u => (
      `<option value="${displayText(u.user_id)}">${displayText(u.user_id)} (${displayText(u.skin_type, '')}/${displayText(u.gender, '')})</option>`
    )).join('');
  } catch(e) {}
  await loadRecommendCategories();
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
    category_group: activeRecommendCategory,
    top_k: 10,
    weights: customized ? weights : null,  // null → server uses YAML config
    shrinkage_k: parseFloat(document.getElementById('shrinkageK').value),
    diversity_weight: parseInt(document.getElementById('diversityW').value) / 100,
  };

  const res = await fetch(API + '/api/recommend', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const data = await res.json();
  recommendHasRun = true;
  renderRecommendResults(data);
}

function renderRecommendResults(data) {
  const results = data.results || [];
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
    </div>`;

  const container = document.getElementById('recResults');
  if (!results.length) {
    container.innerHTML = '<div class="empty">추천 결과 없음 (유저와 정렬된 상품마스터/리뷰그래프/구매 evidence 부족)</div>';
    return;
  }
  container.innerHTML = results.map(r => {
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
          <span class="score">추천점수: ${finalScore} / 정렬점수: ${rankScore} (raw: ${rawScore}, shrink: ${shrinkedScore}, diversity: ${diversityText})</span>
          <div class="explanation">${displayText(r.explanation, '설명 없음')}</div>
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">Evidence:</span> ${evidenceFamilies.length ? evidenceFamilies.map(f => `<span class="chip rel">${displayText(f)}</span>`).join(' ') : '없음'}</span>
          </div>
          <div class="hooks" style="margin-top:8px">
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
            `;
          }).join('') : ''}
          <div class="hooks" style="margin-top:8px">
            <span><span class="label">🔍 탐색:</span> ${displayText(hooks.discovery)}</span>
            <span><span class="label">🤔 고려:</span> ${displayText(hooks.consideration)}</span>
            <span><span class="label">🎯 전환:</span> ${displayText(hooks.conversion)}</span>
          </div>
          ${overlap.length ? `<div style="margin-top:8px">${overlap.map(c => `<span class="chip bee">${displayText(c)}</span>`).join('')}</div>` : ''}
        </div>
      </div>
    `;
  }).join('');
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
  renderGraph(data);
}

const TYPE_COLORS = {
  product: '#6366f1', user: '#8b5cf6', bee_attr: '#f59e0b', keyword: '#eab308',
  context: '#22c55e', concern_pos: '#10b981', concern_neg: '#ef4444',
  tool: '#3b82f6', comparison: '#ec4899', coused: '#f97316',
  brand: '#a78bfa', category: '#67e8f9', ingredient: '#34d399', goal: '#4ade80',
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
loadDashboard();
