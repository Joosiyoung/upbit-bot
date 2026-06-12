const REFRESH_MS = 30000;
let countdownId    = null;
let tradingActive  = false;

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
const fmtKRW = n => {
  if (n === 0) return '0원';
  if (Math.abs(n) >= 1) return Math.round(n).toLocaleString('ko-KR') + '원';
  return n.toFixed(4) + '원';
};
const fmtPct = n => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';

// ── 코인 배지 색상 (해시 기반, 일관된 색상 배정) ──
const BADGE_COLORS = [
  { bg: '#dbe4ff', color: '#3b5bdb' },
  { bg: '#d3f9d8', color: '#2f9e44' },
  { bg: '#c5f6fa', color: '#0b7285' },
  { bg: '#fff3bf', color: '#e67700' },
  { bg: '#ffd8a8', color: '#d9480f' },
  { bg: '#fcc2d7', color: '#c2255c' },
  { bg: '#e8d5fb', color: '#6741d9' },
  { bg: '#d3f9d8', color: '#087f5b' },
];
function getBadgeStyle(ticker) {
  let hash = 0;
  for (let i = 0; i < ticker.length; i++) {
    hash = ((hash << 5) - hash) + ticker.charCodeAt(i);
    hash |= 0;
  }
  const c = BADGE_COLORS[Math.abs(hash) % BADGE_COLORS.length];
  return `background:${c.bg};color:${c.color}`;
}

// ── 보유 코인 상세 카드 ──
function buildCard(h) {
  const profitClass = h.profit_pct >= 0 ? 'profit-pos' : 'profit-neg';
  const profitStr   = fmtPct(h.profit_pct);

  const TF_ORDER = ['day','minute240','minute60'];
  const TF_NAMES = {day:'일봉', minute240:'4시간봉', minute60:'1시간봉'};

  let tabsHTML = '', panelsHTML = '';
  TF_ORDER.forEach((tf, i) => {
    const d = h.timeframes[tf];
    if (!d) return;
    const isActive = i === 0;
    tabsHTML += `<button class="tf-btn${isActive?' active':''}" data-tf="${tf}">${TF_NAMES[tf]} (${d.score>0?'+':''}${d.score})</button>`;

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
    el.textContent = `(${rem}초 후)`;
    if (--rem < 0) { clearInterval(countdownId); el.textContent = ''; fetchAnalysis(); }
  }, 1000);
}

// ── 데이터 fetch ──
async function fetchAnalysis() {
  try {
    const res  = await fetch('/api/analysis');
    const data = await res.json();
    if (data.status === 'initializing') {
      document.getElementById('loading-text').textContent = '첫 분석 중... API 호출 완료를 기다리는 중입니다.';
      setTimeout(fetchAnalysis, 4000);
      return;
    }
    if (data.status === 'error') {
      showError(data.error_msg || '알 수 없는 오류');
      return;
    }
    renderDashboard(data);
    if (tradingActive) startCountdown(REFRESH_MS / 1000);
  } catch(e) {
    showError('서버 연결 실패: ' + e.message);
  }
}

fetchAnalysis();

// ═══════════════════════════════════════════
// 대표 코인 마켓 분석
// ═══════════════════════════════════════════
let marketCountdownId   = null;
let manualRefreshPollId = null;

function buildMarketCard(c) {
  const chgClass = c.change_24h > 0 ? 'pos' : c.change_24h < 0 ? 'neg' : 'zero';
  const chgStr   = (c.change_24h >= 0 ? '+' : '') + c.change_24h.toFixed(2) + '%';
  const rsiClass = c.rsi_class_1h || 'neutral';
  const rsiVal   = c.rsi_1h != null ? c.rsi_1h : 50;
  const aiBadge  = c.ai_analyzed ? ' <span class="ai-badge">✦ AI</span>' : '';
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
    <div class="mcard-bottom">
      <span class="mcard-badge ${c.action_class}">${c.action_text}${aiBadge}</span>
      <div class="mcard-rsi-wrap">
        <span class="mcard-rsi-label">RSI</span>
        <div class="mcard-rsi-bar">
          <div class="mcard-rsi-fill ${rsiClass}" style="width:${rsiVal}%"></div>
        </div>
        <span class="mcard-rsi-val ${rsiClass}">${rsiVal}</span>
      </div>
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
      setTimeout(fetchMarket, 4000);
      return;
    }
    if (data.status === 'ok' && data.coins.length > 0) renderMarket(data);
    if (tradingActive) setTimeout(fetchMarket, REFRESH_MS);
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
    clearInterval(manualRefreshPollId);
    manualRefreshPollId = null;

    const [snapR1, snapR2] = await Promise.all([fetch('/api/analysis'), fetch('/api/market')]);
    const [snap1, snap2]   = await Promise.all([snapR1.json(), snapR2.json()]);
    if (snap1.status === 'ok') renderDashboard(snap1);
    if (snap2.status === 'ok' && snap2.coins?.length) renderMarket(snap2);

    const prevHoldingsAt = snap1.updated_at || '';
    const prevMarketAt   = snap2.updated_at || '';

    await fetch('/api/refresh', {method:'POST'});

    let tries = 0;
    manualRefreshPollId = setInterval(async () => {
      tries++;
      const [r1, r2] = await Promise.all([fetch('/api/analysis'), fetch('/api/market')]);
      const [d1, d2] = await Promise.all([r1.json(), r2.json()]);
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
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

let tradingPollId = null;

function renderTradingStatus(data) {
  // 모드 배지
  const badge = document.getElementById('trade-mode-badge');
  badge.textContent = data.live ? '실거래' : '시뮬레이션';
  badge.className   = 'mode-badge ' + (data.live ? 'live' : 'sim');

  // LIVE 뱃지
  const liveBadge = document.getElementById('live-badge');
  if (liveBadge) liveBadge.style.display = (data.enabled && data.live) ? '' : 'none';

  // dot 점등
  document.getElementById('trade-dot').classList.toggle('active', data.enabled);

  // 버튼 상태
  setStartBtnsDisabled(data.enabled);
  document.getElementById('btn-trade-stop').disabled = !data.enabled;
  document.getElementById('btn-trade-hold').disabled = !data.enabled;

  // 상태 메시지
  document.getElementById('trade-status-msg').textContent = data.status_msg || '—';
  document.getElementById('trade-last-check').textContent  = data.last_check || '—';
  if (data.max_loss != null) {
    document.getElementById('trade-loss').textContent   = `-${data.max_loss}%`;
    document.getElementById('trade-profit').textContent = `+${data.take_profit}%`;
  }

  // ── 보유 포지션 ──
  const positions  = data.positions || {};
  const posTickers = Object.keys(positions);
  const posTbody   = document.getElementById('positions-body');
  const posFoot    = document.getElementById('positions-foot');

  // 합산 수익률 계산
  let totalInvested = 0, totalCurrent = 0;
  posTickers.forEach(ticker => {
    const p = positions[ticker];
    if (p.profit_pct != null && p.amount_krw) {
      totalInvested += p.amount_krw;
      totalCurrent  += p.amount_krw * (1 + p.profit_pct / 100);
    }
  });

  // ── 3-통계 박스 ──
  // 총 투자금
  const investedEl = document.getElementById('stat-invested');
  investedEl.textContent = fmtKRW(totalInvested);
  investedEl.className   = 'trade-stat-value';
  document.getElementById('stat-invested-sub').textContent = `${posTickers.length}종 보유`;

  // 미실현 손익
  const unrealizedKRW = totalCurrent - totalInvested;
  const unrealizedPct = totalInvested > 0 ? unrealizedKRW / totalInvested * 100 : 0;
  const unrealizedEl  = document.getElementById('stat-unrealized');
  unrealizedEl.textContent = (unrealizedKRW >= 0 ? '+' : '') + Math.round(unrealizedKRW).toLocaleString('ko-KR') + '원';
  unrealizedEl.className   = 'trade-stat-value' + (unrealizedKRW > 0 ? ' pos' : unrealizedKRW < 0 ? ' neg' : '');
  document.getElementById('stat-unrealized-sub').textContent = (unrealizedPct >= 0 ? '+' : '') + unrealizedPct.toFixed(2) + '%';

  // 오늘 실현
  const realizedKRW = data.risk?.daily_realized_krw || 0;
  const realizedEl  = document.getElementById('stat-realized');
  realizedEl.textContent = (realizedKRW >= 0 ? '+' : '') + Math.round(realizedKRW).toLocaleString('ko-KR') + '원';
  realizedEl.className   = 'trade-stat-value' + (realizedKRW > 0 ? ' pos' : realizedKRW < 0 ? ' neg' : '');
  const today = new Date().toISOString().slice(0,10);
  const todayTrades = (data.log || []).filter(l => l.type === 'sell' && l.date === today).length;
  document.getElementById('stat-realized-sub').textContent = `${todayTrades}건 매매`;

  // 예산 표시 (포지션 헤더)
  document.getElementById('pos-budget-used').textContent = Math.round(totalInvested).toLocaleString('ko-KR');

  // 포지션 테이블
  if (posTickers.length === 0) {
    posTbody.innerHTML = '<tr><td colspan="6" class="tlog-empty">보유 포지션 없음</td></tr>';
    if (posFoot) posFoot.style.display = 'none';
  } else {
    if (posFoot) posFoot.style.display = '';
    const totalProfitEl   = document.getElementById('pos-total-profit');
    const totalInvestedEl = document.getElementById('pos-total-invested');
    if (totalProfitEl) {
      if (totalInvested > 0) {
        const totalPct  = (totalCurrent - totalInvested) / totalInvested * 100;
        const profitKRW = Math.round(totalCurrent - totalInvested);
        totalProfitEl.textContent = fmtPct(totalPct);
        totalProfitEl.style.color = totalPct >= 0 ? 'var(--red)' : 'var(--blue)';
        if (totalInvestedEl) totalInvestedEl.textContent =
          `투자 ${fmtKRW(totalInvested)} / 평가 ${fmtKRW(Math.round(totalCurrent))} (${profitKRW >= 0 ? '+' : ''}${profitKRW.toLocaleString('ko-KR')}원)`;
      } else {
        totalProfitEl.textContent = '—';
        totalProfitEl.style.color = '';
        if (totalInvestedEl) totalInvestedEl.textContent = '—';
      }
    }

    posTbody.innerHTML = posTickers.map(ticker => {
      const p        = positions[ticker];
      const coin     = ticker.replace('KRW-', '');
      const badgeSt  = getBadgeStyle(coin);
      const entryStr   = fmtKRW(p.entry_price);
      const currentStr = p.current_price ? fmtKRW(p.current_price) : '—';
      const amtStr     = fmtKRW(p.amount_krw);
      let pctStr = '—', pctClass = 'profit-neu';
      if (p.profit_pct != null) {
        pctStr   = fmtPct(p.profit_pct);
        pctClass = p.profit_pct >= 0 ? 'profit-pos' : 'profit-neg';
      }
      return `<tr>
        <td><span class="coin-badge" style="${badgeSt}">${coin}</span></td>
        <td style="color:var(--text-sec)">${entryStr}</td>
        <td style="color:var(--text-pri);font-weight:600">${currentStr}</td>
        <td class="${pctClass}">${pctStr}</td>
        <td style="color:var(--text-sec)">${amtStr}</td>
        <td style="color:var(--text-muted)">${p.entry_time || '—'}</td>
      </tr>`;
    }).join('');
  }

  // ── 거래 이력 ──
  const tbody = document.getElementById('trade-log-body');
  if (!data.log || data.log.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="tlog-empty">자동 매매를 시작하면 여기에 거래 이력이 표시됩니다.</td></tr>';
  } else {
    tbody.innerHTML = data.log.map(r => {
      const typeClass = r.type === 'buy'       ? 'tlog-type-buy'
                      : r.type === 'sell'      ? 'tlog-type-sell'
                      : (r.type === 'buy_fail' || r.type === 'sell_fail') ? 'tlog-type-sell'
                      : 'tlog-type-hold';
      const typeLabel = r.type === 'buy'       ? '매수'
                      : r.type === 'sell'      ? '매도'
                      : r.type === 'buy_fail'  ? '매수실패'
                      : r.type === 'sell_fail' ? '매도실패'
                      : '관망';
      const priceStr  = r.price  ? fmtKRW(r.price)  : '—';
      const amtStr    = r.amount ? fmtKRW(r.amount)  : '—';
      const coin      = String(r.ticker).replace('KRW-', '');
      const badgeSt   = r.ticker !== '-' ? getBadgeStyle(coin) : '';
      const coinCell  = r.ticker !== '-'
        ? `<span class="coin-badge" style="${badgeSt}">${coin}</span>`
        : `<span style="color:var(--text-muted)">-</span>`;
      // 수익률 (매도 시)
      const profitSpan = r.profit_pct != null
        ? `<span class="${r.profit_pct >= 0 ? 'tlog-profit-pos' : 'tlog-profit-neg'}">${r.profit_pct >= 0 ? '+' : ''}${r.profit_pct}%</span>`
        : '';
      const modeBadge = r.live
        ? '<span class="tlog-live">실거래</span>'
        : '<span class="tlog-sim">시뮬</span>';
      return `<tr>
        <td style="color:var(--text-muted);font-size:12px">${r.time}</td>
        <td>
          <div class="tlog-type-cell">
            <span class="${typeClass}">${typeLabel}</span>
            ${profitSpan}
          </div>
        </td>
        <td>${coinCell}</td>
        <td><span class="tlog-reason" title="${escapeHtml(r.reason)}">${escapeHtml(r.reason)}</span></td>
        <td style="color:var(--text-pri);font-weight:600">${priceStr}</td>
        <td style="color:var(--text-pri);font-weight:600">${amtStr}</td>
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
      body: JSON.stringify({live}),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    tradingActive = true;
    await fetchTradingStatus();
    clearInterval(tradingPollId);
    tradingPollId = setInterval(fetchTradingStatus, 10000);
    fetchMarket();
    startCountdown(REFRESH_MS / 1000);
  } catch(e) {
    document.getElementById('trade-status-msg').textContent = '시작 오류: ' + e.message;
    setStartBtnsDisabled(false);
  }
}

async function tradingStartLive() {
  const ok = confirm('⚠️ 실거래 모드\n\n실제 업비트 계좌에서 매수·매도가 실행됩니다.\n계속하시겠습니까?');
  if (!ok) return;
  await tradingStart(true);
}

async function tradingStop(mode) {
  const msg = mode === 'liquidate'
    ? '자동 매매를 중지하고 모든 보유 포지션을 즉시 전액 청산합니다.\n계속하시겠습니까?'
    : '자동 매매를 중지합니다. 보유 코인은 매도하지 않고 그대로 유지됩니다.\n계속하시겠습니까?';
  if (!confirm(msg)) return;
  document.getElementById('btn-trade-stop').disabled = true;
  document.getElementById('btn-trade-hold').disabled = true;
  try {
    tradingActive = false;
    clearInterval(countdownId);
    document.getElementById('countdown').textContent = '';
    await fetch('/api/trading/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode}),
    });
    await fetchTradingStatus();
  } catch(e) {
    document.getElementById('trade-status-msg').textContent = '중지 오류: ' + e.message;
  }
}

// 초기 상태 로드 + 10초 폴링
fetchTradingStatus();
tradingPollId = setInterval(fetchTradingStatus, 10000);
