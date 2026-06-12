const REFRESH_MS = 30000;
let countdownId    = null;
let tradingActive  = false;  // 자동매매 실행 중 여부

// ── 탭 이벤트 위임 ──
document.body.addEventListener('click', e => {
  if (!e.target.classList.contains('tf-btn')) return;
  const card = e.target.closest('.coin-card');
  const tf   = e.target.dataset.tf;
  card.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
  card.querySelectorAll('.tf-panel').forEach(p => p.classList.remove('active'));
  e.target.classList.add('active');
  card.querySelector(`.tf-panel[data-tf="${tf}"]`).classList.add('active');
});

// ── 숫자 포맷 ──
const fmtKRW = n => n >= 1
  ? Math.round(n).toLocaleString('ko-KR') + '원'
  : n.toFixed(4) + '원';
const fmtPct = n => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';

// ── 카드 생성 ──
function buildCard(h) {
  const profitClass = h.profit_pct >= 0 ? 'profit-pos' : 'profit-neg';
  const profitStr   = fmtPct(h.profit_pct);

  // 타임프레임 탭 & 패널
  const TF_ORDER = ['day','minute240','minute60'];
  const TF_NAMES = {day:'일봉', minute240:'4시간봉', minute60:'1시간봉'};

  let tabsHTML = '', panelsHTML = '';
  TF_ORDER.forEach((tf, i) => {
    const d = h.timeframes[tf];
    if (!d) return;
    const isActive = i === 0;

    tabsHTML += `<button class="tf-btn${isActive?' active':''}" data-tf="${tf}">${TF_NAMES[tf]} (${d.score>0?'+':''}${d.score})</button>`;

    // 지지/저항 위치 계산
    const pct = Math.min(100, Math.max(0,
      (d.current - d.support) / (d.resistance - d.support + 0.0001) * 100
    ));

    panelsHTML += `
    <div class="tf-panel${isActive?' active':''}" data-tf="${tf}">
      <div class="indicator-row">
        <span class="ind-label">RSI</span>
        <div class="rsi-bar-wrap">
          <div class="rsi-bar-fill ${d.rsi_class}" style="width:${d.rsi_pct}%"></div>
          <div class="rsi-bar-marker" style="left:30%"></div>
          <div class="rsi-bar-marker" style="left:70%"></div>
        </div>
        <span class="ind-value ${d.rsi_class}">${d.rsi}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">MACD</span>
        <span class="ind-text ${d.macd_class}">${d.macd_label}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">BB</span>
        <span class="ind-text ${d.bb_class}">${d.bb_label}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">EMA</span>
        <span class="ind-text trend-${d.trend_class}">${d.trend_label}</span>
      </div>
      <div class="indicator-row">
        <span class="ind-label">스토캐</span>
        <span class="ind-text ${d.stoch_k < 20 ? 'buy-zone' : d.stoch_k > 80 ? 'sell-zone' : 'neutral'}">
          %K ${d.stoch_k} &nbsp;|&nbsp; 거래량 ${d.volume_ratio}x
        </span>
      </div>
      <div class="sr-row">
        <span class="sr-label">지지 ${fmtKRW(d.support)}</span>
        <div class="sr-bar-wrap">
          <div class="sr-bar-track"></div>
          <div class="sr-bar-dot" style="left:${pct}%"></div>
        </div>
        <span class="sr-label">저항 ${fmtKRW(d.resistance)}</span>
      </div>
    </div>`;
  });

  // 타이밍 가이드
  const buyPts  = (h.guide.buy_points  || []).map(p => `<div class="guide-point">${p}</div>`).join('');
  const sellPts = (h.guide.sell_points || []).map(p => `<div class="guide-point">${p}</div>`).join('');

  return `
  <article class="coin-card ${h.action_class}">
    <div class="card-header">
      <div class="coin-icon">${h.coin.slice(0,3)}</div>
      <span class="coin-name">${h.coin}</span>
      <span class="score-badge">${h.total_score > 0 ? '+' : ''}${h.total_score}점</span>
      <span class="signal-badge ${h.action_class}">${h.action_text}</span>
    </div>

    <div class="price-grid">
      <div class="price-item">
        <span class="price-label">현재가</span>
        <span class="price-value">${fmtKRW(h.current_price)}</span>
      </div>
      <div class="price-item">
        <span class="price-label">평균매수가</span>
        <span class="price-value">${fmtKRW(h.avg_price)}</span>
      </div>
      <div class="price-item">
        <span class="price-label">평가금액</span>
        <span class="price-value">${fmtKRW(h.eval_value)}</span>
      </div>
      <div class="price-item">
        <span class="price-label">손익</span>
        <span class="price-value ${profitClass}">${profitStr}</span>
      </div>
    </div>

    <div class="tf-tabs">${tabsHTML}</div>
    ${panelsHTML}

    <details class="guide-section">
      <summary>매수 / 매도 타이밍 가이드</summary>
      <div class="guide-content">
        <div class="guide-buy">
          <div class="guide-title">매수 포인트</div>
          ${buyPts || '<div class="guide-point">신호 없음</div>'}
        </div>
        <div class="guide-sell">
          <div class="guide-title">매도 포인트</div>
          ${sellPts || '<div class="guide-point">신호 없음</div>'}
        </div>
      </div>
    </details>
  </article>`;
}

// ── 대시보드 렌더링 ──
function renderDashboard(data) {
  document.getElementById('loading').style.display = 'none';
  document.getElementById('error-banner').style.display = 'none';
  document.getElementById('summary-bar').style.display = '';
  document.getElementById('dashboard').style.display = '';
  document.getElementById('footer').style.display = '';

  // 요약 바
  const totalEval = data.holdings.reduce((s, h) => s + h.eval_value, 0);
  document.getElementById('sum-krw').textContent   = fmtKRW(data.krw_balance);
  document.getElementById('sum-eval').textContent  = fmtKRW(totalEval);
  document.getElementById('sum-coins').textContent = `${data.holdings.length}종`;
  if (data.updated_at) {
    const d = new Date(data.updated_at);
    document.getElementById('sum-updated').textContent =
      d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  }
  document.getElementById('foot-strategy').textContent = data.strategy || '—';
  document.getElementById('foot-interval').textContent = data.interval  || '—';

  // 카드 렌더링
  const grid = document.getElementById('holdings-grid');
  grid.innerHTML = data.holdings.map(buildCard).join('');
}

function showError(msg) {
  document.getElementById('loading').style.display = 'none';
  const b = document.getElementById('error-banner');
  b.style.display = 'block';
  b.textContent   = '오류: ' + msg;
}

// ── 카운트다운 ──
function startCountdown(sec) {
  clearInterval(countdownId);
  let rem = sec;
  const el = document.getElementById('countdown');
  countdownId = setInterval(() => {
    el.textContent = `${rem}초 후 갱신`;
    if (--rem < 0) { clearInterval(countdownId); fetchAnalysis(); }
  }, 1000);
}

// ── 데이터 fetch ──
async function fetchAnalysis() {
  try {
    const res  = await fetch('/api/analysis');
    const data = await res.json();

    if (data.status === 'initializing') {
      document.getElementById('loading-text').textContent =
        '첫 분석 중... API 호출 완료를 기다리는 중입니다.';
      setTimeout(fetchAnalysis, 4000);
      return;
    }
    if (data.status === 'error') {
      showError(data.error_msg || '알 수 없는 오류');
      return;
    }

    renderDashboard(data);
    if (tradingActive) startCountdown(REFRESH_MS / 1000);  // 자동매매 중일 때만 루프
  } catch(e) {
    showError('서버 연결 실패: ' + e.message);
  }
}

// 시작
fetchAnalysis();

// ═══════════════════════════════════════════
// 대표 코인 마켓 분석
// ═══════════════════════════════════════════
let marketCountdownId    = null;  // manualRefresh 내부 clearInterval 호환용
let manualRefreshPollId  = null;  // manualRefresh 폴링 인터벌 (중복 생성 방지용)

function buildMarketCard(c) {
  const chgClass = c.change_24h > 0 ? 'pos' : c.change_24h < 0 ? 'neg' : 'zero';
  const chgStr   = (c.change_24h >= 0 ? '+' : '') + c.change_24h.toFixed(2) + '%';
  const scoreStr = (c.total_score >= 0 ? '+' : '') + c.total_score;
  const bbPct    = Math.min(100, Math.max(0, c.bb_pct_1m));
  const aiBadge  = c.ai_analyzed ? '<span class="ai-badge">✦ AI</span>' : '';
  const aiRow    = c.ai_analyzed && c.ai_reason
    ? `<div class="ai-reason">✦ ${c.ai_reason}</div>` : '';

  return `
  <div class="market-card ${c.action_class}">
    <div class="mcard-top">
      <div class="mcard-icon">${c.coin.slice(0,3)}</div>
      <div class="mcard-name-wrap">
        <div class="mcard-coin">${c.coin}</div>
        <div class="mcard-name">${c.name}</div>
      </div>
      <div class="mcard-price-wrap">
        <div class="mcard-price">${fmtKRW(c.current)}</div>
        <div class="mcard-change ${chgClass}">${chgStr}</div>
      </div>
    </div>

    <div class="mcard-signal-row">
      <div style="display:flex;align-items:center;gap:6px">
        <span class="mcard-badge ${c.action_class}">${c.action_text}</span>
        ${aiBadge}
      </div>
      <span class="mcard-score">종합 ${scoreStr}점 &nbsp;|&nbsp; 거래량 ${c.volume_ratio}x</span>
    </div>

    <div class="mcard-inds">
      <div class="mind">
        <span class="mind-label">RSI 1분</span>
        <span class="mind-value ${c.rsi_class_1m}">${c.rsi_1m}</span>
      </div>
      <div class="mind">
        <span class="mind-label">RSI 1시간</span>
        <span class="mind-value ${c.rsi_class_1h}">${c.rsi_1h}</span>
      </div>
      <div class="mind">
        <span class="mind-label">RSI 일봉</span>
        <span class="mind-value ${c.rsi_class_1d}">${c.rsi_1d}</span>
      </div>
      <div class="mind">
        <span class="mind-label">MACD 1분</span>
        <span class="mind-value ${c.macd_class_1m}" style="font-size:10px">${c.macd_label_1m}</span>
      </div>
      <div class="mind">
        <span class="mind-label">EMA 1시간</span>
        <span class="mind-value trend-${c.trend_class_1h}" style="font-size:10px">${c.trend_1h}</span>
      </div>
      <div class="mind">
        <span class="mind-label">EMA 일봉</span>
        <span class="mind-value trend-${c.trend_class_1d}" style="font-size:10px">${c.trend_1d}</span>
      </div>
    </div>

    <div class="mcard-bb-row">
      <span class="mcard-bb-label">${fmtKRW(c.bb_lower_1h)}</span>
      <div class="mcard-bb-bar">
        <div class="mcard-bb-fill" style="width:100%"></div>
        <div class="mcard-bb-dot" style="left:${bbPct}%"></div>
      </div>
      <span class="mcard-bb-label">${fmtKRW(c.bb_upper_1h)}</span>
    </div>
    ${aiRow}
  </div>`;
}

function renderMarket(data) {
  document.getElementById('market-loading').style.display = 'none';
  const grid = document.getElementById('market-grid');
  grid.style.display = '';
  grid.innerHTML = data.coins.map(buildMarketCard).join('');
}

async function fetchMarket() {
  try {
    const res  = await fetch('/api/market');
    const data = await res.json();
    if (data.status === 'initializing') {
      setTimeout(fetchMarket, 4000);  // 초기 로드 대기 (1회성)
      return;  // 이 경로에서는 tradingActive 루프 예약 안 함
    }
    if (data.status === 'ok' && data.coins.length > 0) {
      renderMarket(data);
    }
    if (tradingActive) setTimeout(fetchMarket, REFRESH_MS);  // 자동매매 중일 때만 루프
  } catch(e) {
    console.warn('fetchMarket error:', e);
    if (tradingActive) setTimeout(fetchMarket, REFRESH_MS);
  }
}

async function manualRefresh() {
  const btn = document.getElementById('btn-refresh');
  btn.disabled = true;
  btn.textContent = '갱신 중...';
  clearInterval(countdownId);
  clearInterval(marketCountdownId);
  document.getElementById('countdown').textContent = '';

  try {
    // 이전 폴링이 남아있으면 먼저 제거 (중복 setInterval 방지)
    clearInterval(manualRefreshPollId);
    manualRefreshPollId = null;

    // 현재 캐시로 즉시 화면 갱신 (요약 바 포함)
    const [snapR1, snapR2] = await Promise.all([
      fetch('/api/analysis'),
      fetch('/api/market'),
    ]);
    const [snap1, snap2] = await Promise.all([snapR1.json(), snapR2.json()]);
    if (snap1.status === 'ok') renderDashboard(snap1);
    if (snap2.status === 'ok' && snap2.coins?.length) renderMarket(snap2);

    const prevHoldingsAt = snap1.updated_at || '';
    const prevMarketAt   = snap2.updated_at || '';

    await fetch('/api/refresh', {method:'POST'});

    let tries = 0;
    manualRefreshPollId = setInterval(async () => {
      tries++;
      const [r1, r2] = await Promise.all([
        fetch('/api/analysis'),
        fetch('/api/market'),
      ]);
      const [d1, d2] = await Promise.all([r1.json(), r2.json()]);

      // updated_at이 바뀌었을 때만 새 데이터로 판단
      const holdingsOk = d1.status === 'ok' && d1.updated_at !== prevHoldingsAt;
      const marketOk   = d2.status === 'ok' && d2.coins.length > 0 && d2.updated_at !== prevMarketAt;

      if (holdingsOk) renderDashboard(d1);
      if (marketOk)   renderMarket(d2);

      if ((holdingsOk && marketOk) || tries > 20) {
        clearInterval(manualRefreshPollId);
        manualRefreshPollId = null;
        startCountdown(REFRESH_MS / 1000);
        btn.disabled = false;
        btn.textContent = '새로고침';
      }
    }, 3000);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '새로고침';
  }
}

fetchMarket();

// ═══════════════════════════════════════════
// 자동 매매
// ═══════════════════════════════════════════

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

let tradingPollId = null;

function renderTradingStatus(data) {
  // 모드 배지
  const badge = document.getElementById('trade-mode-badge');
  badge.textContent = data.live ? '실거래' : '시뮬레이션';
  badge.className   = 'mode-badge ' + (data.live ? 'live' : 'sim');

  // 점등
  const dot = document.getElementById('trade-dot');
  dot.classList.toggle('active', data.enabled);

  // 버튼 상태
  setStartBtnsDisabled(data.enabled);
  document.getElementById('btn-trade-stop').disabled = !data.enabled;
  document.getElementById('btn-trade-hold').disabled = !data.enabled;

  // 상태 메시지
  document.getElementById('trade-status-msg').textContent = data.status_msg || '—';
  document.getElementById('trade-last-check').textContent  = data.last_check || '—';

  // 설정값
  if (data.max_loss != null) {
    document.getElementById('trade-loss').textContent   = `-${data.max_loss}%`;
    document.getElementById('trade-profit').textContent = `+${data.take_profit}%`;
  }

  // 예산 현황
  if (data.used_budget != null) {
    document.getElementById('pos-budget-used').textContent  = Math.round(data.used_budget).toLocaleString('ko-KR');
    // total_budget 제거됨 → 시뮬은 sim_krw, 실거래는 used_budget 기준으로 표시
    const totalDisplay = data.live
      ? Math.round(data.used_budget).toLocaleString('ko-KR')
      : (data.sim_krw != null ? Math.round(data.sim_krw + data.used_budget).toLocaleString('ko-KR') : '—');
    document.getElementById('pos-budget-total').textContent = totalDisplay;
  }

  // 보유 포지션
  const posTbody = document.getElementById('positions-body');
  const positions = data.positions || {};
  const posTickers = Object.keys(positions);
  const totalProfitEl   = document.getElementById('pos-total-profit');
  const totalInvestedEl = document.getElementById('pos-total-invested');
  const posFoot         = document.getElementById('positions-foot');
  if (posTickers.length === 0) {
    posTbody.innerHTML = '<tr><td colspan="6" class="tlog-empty">보유 포지션 없음</td></tr>';
    if (posFoot) posFoot.style.display = 'none';
  } else {
    // 합산 수익률: 투자금 가중 평균
    let totalInvested = 0, totalCurrent = 0;
    posTickers.forEach(ticker => {
      const p = positions[ticker];
      if (p.profit_pct != null && p.amount_krw) {
        totalInvested += p.amount_krw;
        totalCurrent  += p.amount_krw * (1 + p.profit_pct / 100);
      }
    });
    if (posFoot) posFoot.style.display = '';
    if (totalProfitEl) {
      if (totalInvested > 0) {
        const totalPct = (totalCurrent - totalInvested) / totalInvested * 100;
        const profitKRW = Math.round(totalCurrent - totalInvested);
        totalProfitEl.textContent = fmtPct(totalPct);
        totalProfitEl.style.color = totalPct >= 0 ? 'var(--red)' : 'var(--blue)';
        if (totalInvestedEl) totalInvestedEl.textContent = `투자 ${fmtKRW(totalInvested)} / 평가 ${fmtKRW(Math.round(totalCurrent))} (${profitKRW >= 0 ? '+' : ''}${profitKRW.toLocaleString('ko-KR')}원)`;
      } else {
        totalProfitEl.textContent = '—';
        totalProfitEl.style.color = '';
        if (totalInvestedEl) totalInvestedEl.textContent = '—';
      }
    }
    posTbody.innerHTML = posTickers.map(ticker => {
      const p = positions[ticker];
      const coin = ticker.replace('KRW-', '');
      const entryStr   = fmtKRW(p.entry_price);
      const currentStr = p.current_price ? fmtKRW(p.current_price) : '—';
      const amtStr     = fmtKRW(p.amount_krw);
      let pctStr = '—', pctClass = 'profit-neu';
      if (p.profit_pct != null) {
        pctStr   = fmtPct(p.profit_pct);
        pctClass = p.profit_pct >= 0 ? 'profit-pos' : 'profit-neg';
      }
      return `<tr>
        <td style="font-weight:700;color:var(--text-pri)">${coin}</td>
        <td>${entryStr}</td>
        <td>${currentStr}</td>
        <td class="${pctClass}">${pctStr}</td>
        <td>${amtStr}</td>
        <td>${p.entry_time || '—'}</td>
      </tr>`;
    }).join('');
  }

  // 거래 이력
  const tbody = document.getElementById('trade-log-body');
  if (!data.log || data.log.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="tlog-empty">자동 매매를 시작하면 여기에 거래 이력이 표시됩니다.</td></tr>';
  } else {
    tbody.innerHTML = data.log.map(r => {
      const typeClass = r.type === 'buy' ? 'tlog-type-buy'
                      : r.type === 'sell' ? 'tlog-type-sell'
                      : (r.type === 'buy_fail' || r.type === 'sell_fail') ? 'tlog-type-sell'
                      : 'tlog-type-hold';
      const typeLabel = r.type === 'buy' ? '매수'
                      : r.type === 'sell' ? '매도'
                      : r.type === 'buy_fail' ? '매수실패'
                      : r.type === 'sell_fail' ? '매도실패'
                      : '관망';
      const priceStr  = r.price  ? fmtKRW(r.price)  : '—';
      const amtStr    = r.amount ? fmtKRW(r.amount)  : '—';
      const modeBadge = r.live ? '<span class="tlog-live">실거래</span>' : '<span class="tlog-sim">시뮬</span>';
      return `<tr>
        <td>${r.time}</td>
        <td class="${typeClass}">${typeLabel}</td>
        <td style="font-weight:600;color:var(--text-pri)">${escapeHtml(r.ticker)}</td>
        <td>${escapeHtml(r.reason)}</td>
        <td>${priceStr}</td>
        <td>${amtStr}</td>
        <td>${modeBadge}</td>
      </tr>`;
    }).join('');
  }
}

async function fetchTradingStatus() {
  try {
    const res  = await fetch('/api/trading/status');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    renderTradingStatus(data);
  } catch(e) {
    document.getElementById('trade-status-msg').textContent = '연결 오류: ' + e.message;
    console.error('fetchTradingStatus error:', e);
  }
}

function setStartBtnsDisabled(disabled) {
  document.getElementById('btn-trade-sim').disabled  = disabled;
  document.getElementById('btn-trade-live').disabled = disabled;
}

async function tradingStart(live) {
  setStartBtnsDisabled(true);
  try {
    const res = await fetch('/api/trading/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({live: live}),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    tradingActive = true;
    await fetchTradingStatus();
    clearInterval(tradingPollId);
    tradingPollId = setInterval(fetchTradingStatus, 10000);
    fetchMarket();                       // 시작 시 시장 분석 즉시 갱신 (이후 루프)
    startCountdown(REFRESH_MS / 1000);   // 카운트다운 시작 (이후 루프)
  } catch(e) {
    document.getElementById('trade-status-msg').textContent = '시작 오류: ' + e.message;
    setStartBtnsDisabled(false);
    console.error('tradingStart error:', e);
  }
}

async function tradingStartLive() {
  const ok = confirm(
    '⚠️ 실거래 모드\n\n실제 업비트 계좌에서 매수·매도가 실행됩니다.\n계속하시겠습니까?'
  );
  if (!ok) return;
  await tradingStart(true);
}

async function tradingStop(mode) {
  const msg = mode === 'liquidate'
    ? '자동 매매를 중지하고 모든 보유 포지션을 즉시 전액 청산합니다.\n계속하시겠습니까?'
    : '자동 매매를 중지합니다. 보유 코인은 매도하지 않고 그대로 유지됩니다.\n계속하시겠습니까?';
  const ok = confirm(msg);
  if (!ok) return;
  document.getElementById('btn-trade-stop').disabled = true;
  document.getElementById('btn-trade-hold').disabled = true;
  try {
    tradingActive = false;
    clearInterval(countdownId);
    document.getElementById('countdown').textContent = '';
    await fetch('/api/trading/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode})
    });
    await fetchTradingStatus();
  } catch(e) {
    document.getElementById('trade-status-msg').textContent = '중지 오류: ' + e.message;
    console.error('tradingStop error:', e);
  }
}

// 초기 상태 로드 + 10초 폴링 시작
fetchTradingStatus();
tradingPollId = setInterval(fetchTradingStatus, 10000);
