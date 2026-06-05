// Shared rendering logic for the stock side-panel / standalone page.
// Phase 1: a lightweight snapshot (hero strip + perf row + 5Y price chart).
// LLM-generated insight panels come in a later phase.
(function (global) {

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtPct(v) {
    if (v == null || isNaN(v)) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${(v * 100).toFixed(1)}%`;
  }
  function fmtMoney(v, ccy) {
    if (v == null) return '—';
    const sym = (!ccy || ccy === 'USD') ? '$' : '';
    const suffix = (!ccy || ccy === 'USD') ? '' : ' ' + ccy;
    let s;
    if (v >= 1e12) s = `${(v / 1e12).toFixed(2)}T`;
    else if (v >= 1e9) s = `${(v / 1e9).toFixed(1)}B`;
    else if (v >= 1e6) s = `${(v / 1e6).toFixed(0)}M`;
    else s = `${v.toFixed(0)}`;
    return `${sym}${s}${suffix}`;
  }
  function apiUrl(path) {
    // Same-origin when served by FastAPI; allows file:// dev against localhost.
    if (location.protocol === 'file:') return 'http://127.0.0.1:8000' + path;
    return path;
  }

  function niceTicks(min, max, count) {
    count = count || 5;
    const range = max - min;
    if (range <= 0) return [min];
    const rawStep = range / (count - 1);
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const norm = rawStep / mag;
    let niceStep;
    if (norm < 1.5) niceStep = 1 * mag;
    else if (norm < 3) niceStep = 2 * mag;
    else if (norm < 7) niceStep = 5 * mag;
    else niceStep = 10 * mag;
    const niceMin = Math.floor(min / niceStep) * niceStep;
    const ticks = [];
    for (let v = niceMin; v <= max + niceStep / 2; v += niceStep) {
      if (v >= min - niceStep / 2) ticks.push(v);
    }
    return ticks;
  }

  function priceChartSVG(points, opts) {
    if (!points || points.length < 2) return '<div class="text-xs text-gray-400">Not enough price history to chart.</div>';
    const { h = 220, color = '#2563eb', fillTo = '#dbeafe', ccy = 'USD' } = opts || {};
    const W = 1000, H = h;
    const margin = { top: 10, right: 14, bottom: 24, left: 60 };
    const cw = W - margin.left - margin.right;
    const ch = H - margin.top - margin.bottom;

    const ys = points.map(p => p.close);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const yTicks = niceTicks(minY, maxY, 5);
    const yLo = Math.min(minY, yTicks[0]);
    const yHi = Math.max(maxY, yTicks[yTicks.length - 1]);
    const yRange = (yHi - yLo) || 1;
    const xToPx = i => margin.left + (i / (points.length - 1)) * cw;
    const yToPx = v => margin.top + ch - ((v - yLo) / yRange) * ch;
    const moneyLabel = (!ccy || ccy === 'USD') ? (t => `$${t.toFixed(0)}`) : (t => t.toFixed(0));

    const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xToPx(i).toFixed(1)},${yToPx(p.close).toFixed(1)}`).join(' ');
    const fillPath = `${path} L${xToPx(points.length - 1).toFixed(1)},${(margin.top + ch).toFixed(1)} L${margin.left.toFixed(1)},${(margin.top + ch).toFixed(1)} Z`;

    const yGrid = yTicks.map(t => {
      const y = yToPx(t).toFixed(1);
      return `<line x1="${margin.left}" x2="${W - margin.right}" y1="${y}" y2="${y}" stroke="#e5e7eb" stroke-width="1"/>
              <text x="${margin.left - 6}" y="${y}" text-anchor="end" alignment-baseline="middle"
                font-size="11" fill="#6b7280">${moneyLabel(t)}</text>`;
    }).join('');

    const yearTicks = [];
    let lastYear = null;
    points.forEach((p, i) => {
      const y = p.date ? p.date.slice(0, 4) : null;
      if (y && y !== lastYear) { yearTicks.push({ i, year: y }); lastYear = y; }
    });
    const xLabels = yearTicks.slice(1).map(t => {
      const x = xToPx(t.i).toFixed(1);
      return `<line x1="${x}" x2="${x}" y1="${margin.top}" y2="${margin.top + ch}" stroke="#f3f4f6" stroke-width="1"/>
              <text x="${x}" y="${(margin.top + ch + 16).toFixed(1)}" text-anchor="middle"
                font-size="11" fill="#6b7280">${t.year}</text>`;
    }).join('');

    const startY = yToPx(points[0].close).toFixed(1);
    const lastIdx = points.length - 1;
    const lastX = xToPx(lastIdx).toFixed(1);
    const lastYpx = yToPx(points[lastIdx].close).toFixed(1);

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
      ${yGrid}
      ${xLabels}
      <line x1="${margin.left}" x2="${W - margin.right}" y1="${startY}" y2="${startY}"
            stroke="#9ca3af" stroke-width="1" stroke-dasharray="3 3" opacity="0.5"/>
      <path d="${fillPath}" fill="${fillTo}" opacity="0.45" />
      <path d="${path}" fill="none" stroke="${color}" stroke-width="2" />
      <circle cx="${lastX}" cy="${lastYpx}" r="3.5" fill="${color}" />
      <line x1="${margin.left}" x2="${margin.left}" y1="${margin.top}" y2="${margin.top + ch}" stroke="#9ca3af" stroke-width="1"/>
      <line x1="${margin.left}" x2="${W - margin.right}" y1="${margin.top + ch}" y2="${margin.top + ch}" stroke="#9ca3af" stroke-width="1"/>
    </svg>`;
  }

  // Render the Phase-1 snapshot into `target`.
  // opts: { heatRow, primaryBenchmark, snapshot, compact, notes }
  function renderSnapshot(target, opts) {
    opts = opts || {};
    const snap = opts.snapshot;
    const heatRow = opts.heatRow || null;
    const benchR = opts.primaryBenchmark ? opts.primaryBenchmark.returns : null;
    const benchName = opts.primaryBenchmark ? opts.primaryBenchmark.name : 'benchmark';
    const ccy = (snap && snap.currency) || 'USD';

    if (!snap) {
      target.innerHTML = `<div class="text-sm text-gray-600">No price data for this ticker yet. Run
        <code class="bg-gray-100 px-1 rounded">python -m backend.etl refresh ${escapeHtml(opts.ticker || '')}</code>.</div>`;
      return;
    }

    const chg = snap.day_change_pct;
    const chgCls = chg == null ? 'text-gray-500' : chg >= 0 ? 'text-green-600' : 'text-red-600';
    const priceStr = ((!ccy || ccy === 'USD') ? '$' : '') +
      snap.latest_close.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) +
      ((!ccy || ccy === 'USD') ? '' : ' ' + ccy);

    const HORIZONS = ['1y', '3y', '5y', '10y'];
    const perfRows = HORIZONS.map(h => {
      const v = heatRow ? heatRow.returns[h] : null;
      const b = benchR ? benchR[h] : null;
      const d = (v == null || b == null) ? null : v - b;
      const vCls = v == null ? 'text-gray-400' : v >= 0 ? 'text-green-700' : 'text-red-700';
      const dStr = d == null ? '—' : `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)} pp`;
      const dCls = d == null ? 'text-gray-400' : d >= 0 ? 'text-green-700' : 'text-red-700';
      return `<tr>
        <td class="py-1 pr-4 text-gray-500">${h.toUpperCase()}</td>
        <td class="py-1 pr-4 text-right font-medium ${vCls}">${fmtPct(v)}</td>
        <td class="py-1 text-right ${dCls}">${dStr}</td>
      </tr>`;
    }).join('');

    const tile = (label, val) => `
      <div class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
        <div class="text-[11px] uppercase tracking-wide text-gray-500">${label}</div>
        <div class="text-sm font-semibold text-gray-900 mt-0.5">${val}</div>
      </div>`;

    const chartH = opts.compact ? 200 : 260;
    const notesHtml = opts.notes
      ? `<div class="mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">${escapeHtml(opts.notes)}</div>`
      : '';

    target.innerHTML = `
      <div class="flex items-baseline gap-3">
        <div class="text-3xl font-bold tabular-nums">${priceStr}</div>
        <div class="text-sm font-medium ${chgCls}">${chg == null ? '' : (chg >= 0 ? '▲' : '▼') + ' ' + fmtPct(chg)}</div>
      </div>
      <div class="text-xs text-gray-500 mt-0.5">Last close ${escapeHtml(snap.as_of_date)}${ccy && ccy !== 'USD' ? ' · ' + escapeHtml(ccy) : ''}</div>

      <div class="grid grid-cols-3 gap-2 mt-4">
        ${tile('Market cap', fmtMoney(snap.market_cap, ccy))}
        ${tile('P/E', snap.pe_ratio == null ? '—' : snap.pe_ratio.toFixed(1))}
        ${tile('Div yield', snap.dividend_yield == null ? '—' : (snap.dividend_yield * 100).toFixed(2) + '%')}
      </div>

      <div class="mt-5">
        <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Annualized total return</div>
        <table class="w-full text-sm">
          <thead><tr class="text-[11px] text-gray-400">
            <th class="text-left font-normal pr-4"></th>
            <th class="text-right font-normal pr-4">Return</th>
            <th class="text-right font-normal">vs ${escapeHtml(benchName)}</th>
          </tr></thead>
          <tbody>${perfRows}</tbody>
        </table>
      </div>

      <div class="mt-5">
        <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">5-year price</div>
        ${priceChartSVG(snap.sparkline_5y, { h: chartH, ccy })}
      </div>
      ${notesHtml}
      <div class="mt-4 text-[11px] text-gray-400">Prices via Yahoo Finance (15+ min delayed). Personal research only — not investment advice.</div>
    `;
  }

  // ---- Phase 2/3: derived metrics tiles + LLM insight panel ----

  function metricsTiles(m) {
    if (!m || !m.exists) return '';
    const ccy = m.financial_currency || 'USD';
    const tile = (label, value, sub, tip) => `
      <div class="rounded-lg border border-gray-200 bg-white px-3 py-2" ${tip ? `title="${escapeHtml(tip)}"` : ''}>
        <div class="text-[11px] uppercase tracking-wide text-gray-500">${escapeHtml(label)}</div>
        <div class="text-base font-bold text-gray-900 mt-0.5 leading-tight">${value}</div>
        <div class="text-[11px] text-gray-400 mt-0.5">${escapeHtml(sub || '')}</div>
      </div>`;
    const pct = (v) => v == null ? '—' : (v * 100).toFixed(0) + '%';
    const opmBasis = m.operating_margin_basis === 'NetIncome/Revenue (fallback)'
      ? 'Net income / revenue (Op. income N/A)' : 'Operating income / revenue';
    const src = (m.sources || 'yfinance') + (m.last_updated_at ? ' · ' + new Date(m.last_updated_at).toLocaleDateString() : '');
    return `
      <div class="mb-4">
        <div class="flex items-center justify-between mb-1.5">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-500">Key business metrics</div>
          <div class="text-[10px] text-gray-400">source: ${escapeHtml(src)}</div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
          ${tile('Revenue', fmtMoney(m.revenue_latest, ccy), m.revenue_latest_period || '')}
          ${tile('Gross margin', pct(m.gross_margin), 'Latest FY')}
          ${tile('Operating margin', pct(m.operating_margin), 'Latest FY', opmBasis)}
          ${tile('Revenue 3Y CAGR', m.revenue_3y_cagr == null ? '—' : fmtPct(m.revenue_3y_cagr), m.revenue_cagr_window || '3-year compound')}
        </div>
        ${ccy !== 'USD' ? `<div class="text-[11px] text-amber-700 mt-1">Revenue reported in ${escapeHtml(ccy)} (foreign filer); margins & CAGR are currency-neutral.</div>` : ''}
      </div>`;
  }

  function driverRow(d) {
    const arrow = d.direction === 'Tailwind' ? '↑' : d.direction === 'Headwind' ? '↓' : '→';
    const cls = d.direction === 'Tailwind' ? 'text-green-700' : d.direction === 'Headwind' ? 'text-red-700' : 'text-amber-600';
    const ev = (d.evidence || []).length
      ? '<span class="ml-auto">' + d.evidence.map((e, i) =>
          `<a class="text-[10px] text-blue-600 hover:underline" href="${escapeHtml(e.source_url)}" target="_blank" rel="noopener">[${i + 1}]</a>`).join(' ') + '</span>'
      : '';
    return `<div class="py-2 border-b border-gray-100 last:border-0">
      <div class="flex items-center gap-2 mb-0.5">
        <span class="text-base font-bold ${cls} leading-none w-3">${arrow}</span>
        <span class="font-semibold text-sm text-gray-900">${escapeHtml(d.factor)}</span>
        ${ev}
      </div>
      <div class="text-xs text-gray-700 ml-5 leading-relaxed">${escapeHtml(d.description)}</div>
    </div>`;
  }

  function horizonPanel(h, id, hidden) {
    const internal = (h && h.internal) || [];
    const macro = (h && h.macro) || [];
    const empty = '<div class="text-xs text-gray-400 py-2">—</div>';
    const colTitle = (t) => `<div class="text-[10px] uppercase tracking-widest text-gray-500 font-semibold mb-2 pb-2 border-b border-gray-100">${t}</div>`;
    return `<div data-horizon-panel="${id}" class="${hidden ? 'hidden' : ''}">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="border border-gray-200 px-4 py-3 bg-white">
          ${colTitle('Internal · what the company controls')}
          ${internal.length ? internal.map(driverRow).join('') : empty}
        </div>
        <div class="border border-gray-200 px-4 py-3 bg-white">
          ${colTitle('Macro · exogenous forces')}
          ${macro.length ? macro.map(driverRow).join('') : empty}
        </div>
      </div>
    </div>`;
  }

  function insightBlock(target, rec) {
    if (!rec || !rec.exists) {
      return `<div class="border border-dashed border-gray-300 rounded-lg p-4 text-sm text-gray-600">
        No AI deep-dive generated for this name yet. Generate one with
        <code class="bg-gray-100 px-1 rounded text-xs">python -m backend.insights refresh stock TICKER</code>
        (needs <code class="bg-gray-100 px-1 rounded text-xs">ANTHROPIC_API_KEY</code>), or hand-seed via
        <code class="bg-gray-100 px-1 rounded text-xs">python -m scripts.seed_insights</code>.</div>`;
    }
    const c = rec.content;
    const UID = 'h' + Math.random().toString(36).slice(2, 8);

    const tldr = c.tldr ? `<div class="border-l-2 border-gray-300 pl-3 py-1 mb-4 text-sm text-gray-700 italic leading-relaxed">${escapeHtml(c.tldr)}</div>` : '';

    const keyMetric = c.key_metric_name ? `
      <div class="border border-gray-200 rounded-lg px-4 py-3 mb-4 bg-white">
        <div class="text-[10px] uppercase tracking-widest text-gray-500 mb-1">AI value-chain lens</div>
        <div class="flex items-baseline justify-between flex-wrap gap-x-3">
          <div class="text-lg font-bold text-gray-900">${escapeHtml(c.key_metric_name)}</div>
          <div class="font-mono text-base text-gray-700">${escapeHtml(c.key_metric_3y_change || '')}</div>
        </div>
        <div class="text-xs text-gray-600 mt-1.5 leading-relaxed">${escapeHtml(c.key_metric_explanation || '')}</div>
      </div>` : '';

    const HZ = [['1y', 'Past 1Y'], ['3y', 'Past 3Y'], ['forward', 'Forward 12-18mo']];
    const driversBlock = (c.drivers_1y && c.drivers_3y && c.drivers_forward) ? `
      <div class="mb-4">
        <div class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Drivers</div>
        <div class="flex gap-1 mb-3 p-1 bg-gray-100 rounded-md w-fit text-xs" data-hz-bar="${UID}">
          ${HZ.map(([id, lbl], i) => `<button data-hz="${id}" data-hz-bar="${UID}"
            class="px-3 py-1.5 rounded ${i === 0 ? 'bg-white shadow-sm font-semibold text-gray-900' : 'text-gray-600 hover:text-gray-900'}">${lbl}</button>`).join('')}
        </div>
        <div data-hz-panels="${UID}">
          ${horizonPanel(c.drivers_1y, '1y', false)}
          ${horizonPanel(c.drivers_3y, '3y', true)}
          ${horizonPanel(c.drivers_forward, 'forward', true)}
        </div>
      </div>` : '';

    const guidance = c.mgmt_guidance ? `
      <div class="border border-gray-200 rounded-lg px-4 py-3 mb-4 bg-white">
        <div class="text-[10px] uppercase tracking-widest text-gray-500 mb-1">Management guidance</div>
        <div class="text-sm text-gray-900 leading-relaxed">${escapeHtml(c.mgmt_guidance)}</div>
      </div>` : '';

    const list = (title, items, cls, arrow) => `
      <div class="border border-gray-200 rounded-lg px-4 py-3 bg-white">
        <div class="text-[10px] uppercase tracking-widest ${cls} font-semibold mb-2">${title}</div>
        <ul class="text-sm text-gray-800 space-y-1">
          ${items.map(x => `<li class="flex gap-2"><span class="${cls}">${arrow}</span><span>${escapeHtml(x)}</span></li>`).join('')}
        </ul>
      </div>`;
    const catRisk = (c.key_catalysts?.length || c.key_risks?.length) ? `
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        ${c.key_catalysts?.length ? list('Catalysts', c.key_catalysts, 'text-green-700', '↑') : '<div></div>'}
        ${c.key_risks?.length ? list('Risks', c.key_risks, 'text-red-700', '↓') : '<div></div>'}
      </div>` : '';

    const footer = `<div class="text-[11px] text-gray-400 mt-2">As of ${escapeHtml(c.as_of || '')} · generated ${rec.generated_at ? new Date(rec.generated_at).toLocaleDateString() : ''} · ${escapeHtml(rec.model || '')} · AI-generated, verify before acting — not investment advice.</div>`;

    return `<div data-insight-uid="${UID}">${tldr}${keyMetric}${driversBlock}${guidance}${catRisk}${footer}</div>`;
  }

  function wireHorizonTabs(target) {
    target.querySelectorAll('button[data-hz]').forEach(btn => {
      btn.addEventListener('click', () => {
        const uid = btn.getAttribute('data-hz-bar');
        const id = btn.dataset.hz;
        target.querySelectorAll(`button[data-hz][data-hz-bar="${uid}"]`).forEach(b => {
          const on = b.dataset.hz === id;
          b.classList.toggle('bg-white', on);
          b.classList.toggle('shadow-sm', on);
          b.classList.toggle('font-semibold', on);
          b.classList.toggle('text-gray-900', on);
          b.classList.toggle('text-gray-600', !on);
          b.classList.toggle('hover:text-gray-900', !on);
        });
        const panels = target.querySelector(`[data-hz-panels="${uid}"]`);
        if (panels) panels.querySelectorAll('[data-horizon-panel]').forEach(p => {
          p.classList.toggle('hidden', p.dataset.horizonPanel !== id);
        });
      });
    });
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n >= 1024 * 1024) return (n / 1048576).toFixed(1) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return n + ' B';
  }

  // A compact inline sparkline. `vals` is an array of numbers (nulls allowed and
  // skipped). Draws a baseline area + line with a dot on the most recent point.
  function sparklineSVG(vals, opts) {
    const { w = 150, h = 38, color = '#2563eb', fill = '#dbeafe' } = opts || {};
    const pts = vals.map((v, i) => ({ i, v })).filter(p => p.v != null && !isNaN(p.v));
    if (pts.length < 2) return '<div class="text-xs text-gray-400">—</div>';
    const pad = 3;
    const xs = vals.length - 1 || 1;
    const ys = pts.map(p => p.v);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi === lo) { hi += 1; lo -= 1; }
    const xToPx = i => pad + (i / xs) * (w - 2 * pad);
    const yToPx = v => pad + (1 - (v - lo) / (hi - lo)) * (h - 2 * pad);
    const line = pts.map((p, k) => `${k === 0 ? 'M' : 'L'}${xToPx(p.i).toFixed(1)},${yToPx(p.v).toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    const area = `${line} L${xToPx(last.i).toFixed(1)},${(h - pad).toFixed(1)} L${xToPx(pts[0].i).toFixed(1)},${(h - pad).toFixed(1)} Z`;
    const rising = last.v >= pts[0].v;
    const stroke = color || (rising ? '#16a34a' : '#dc2626');
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
      <path d="${area}" fill="${fill}" opacity="0.4"/>
      <path d="${line}" fill="none" stroke="${stroke}" stroke-width="1.5"/>
      <circle cx="${xToPx(last.i).toFixed(1)}" cy="${yToPx(last.v).toFixed(1)}" r="2.5" fill="${stroke}"/>
    </svg>`;
  }

  // Multi-year fundamentals trend sparklines (revenue + gross/operating margin),
  // from /api/fundamentals/{ticker}. All figures are reported-currency / verbatim.
  function fundamentalsBlock(rec) {
    if (!rec || !rec.exists || !rec.points || rec.points.length < 2) return '';
    const pts = rec.points;
    const ccy = rec.currency || 'USD';
    const span = `${pts[0].period} – ${pts[pts.length - 1].period}`;
    const card = (label, vals, latestStr, fmtColor) => {
      const shown = vals.filter(v => v != null);
      if (shown.length < 2) return '';
      return `<div class="rounded-lg border border-gray-200 bg-white px-3 py-2">
        <div class="flex items-baseline justify-between mb-1">
          <div class="text-[10px] uppercase tracking-widest text-gray-500">${label}</div>
          <div class="text-sm font-semibold ${fmtColor || 'text-gray-900'}">${latestStr}</div>
        </div>
        ${sparklineSVG(vals, { color: fmtColor ? null : '#2563eb' })}
      </div>`;
    };
    const rev = pts.map(p => p.revenue);
    const gm = pts.map(p => p.gross_margin == null ? null : p.gross_margin * 100);
    const om = pts.map(p => p.operating_margin == null ? null : p.operating_margin * 100);
    const lastRev = rev[rev.length - 1];
    const lastGm = [...gm].reverse().find(v => v != null);
    const lastOm = [...om].reverse().find(v => v != null);
    const cards = [
      card('Revenue', rev, fmtMoney(lastRev, ccy), null),
      card('Gross margin', gm, lastGm == null ? '—' : lastGm.toFixed(0) + '%', null),
      card('Operating margin', om, lastOm == null ? '—' : lastOm.toFixed(0) + '%', null),
    ].filter(Boolean);
    if (!cards.length) return '';
    return `<div>
      <div class="grid grid-cols-3 gap-2">${cards.join('')}</div>
      <div class="text-[11px] text-gray-400 mt-2">Fiscal-year trend ${span}${ccy !== 'USD' ? ' · ' + escapeHtml(ccy) : ''} · from reported fundamentals.</div>
    </div>`;
  }

  // SEC EDGAR filings, grouped by type, newest-first. `filings` is the array
  // from /api/filings/{ticker}. Foreign filers show 20-F/6-K instead of 10-K/10-Q.
  function filingsBlock(filings) {
    if (!filings || !filings.length) {
      return `<div class="text-sm text-gray-500">No SEC filings stored. Run
        <code class="text-xs">python -m backend.filings refresh ${''}&lt;ticker&gt;</code>.
        Foreign listings (Samsung, SK hynix) don't file with the SEC.</div>`;
    }
    const LABEL = {
      '10-K': 'Annual (10-K)', '20-F': 'Annual (20-F)', '40-F': 'Annual (40-F)',
      '10-Q': 'Quarterly (10-Q)', '8-K-earnings': 'Earnings (8-K)', '6-K': 'Interim (6-K)',
    };
    const order = ['10-K', '20-F', '40-F', '10-Q', '8-K-earnings', '6-K'];
    const groups = {};
    for (const f of filings) (groups[f.filing_type] = groups[f.filing_type] || []).push(f);
    const types = Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b));

    const section = (t) => {
      const rows = groups[t].map(f => {
        const when = f.period_end || f.filed_at;
        return `<li class="flex items-center justify-between py-1 border-b border-gray-100 last:border-0">
          <span class="text-sm">${escapeHtml(when)}
            <span class="text-xs text-gray-400 ml-1">filed ${escapeHtml(f.filed_at)}</span></span>
          <span class="text-xs text-gray-400">${fmtBytes(f.size_bytes)}
            <a href="${escapeHtml(f.primary_doc_url)}" target="_blank" rel="noopener"
               class="text-blue-600 hover:underline ml-2">SEC ↗</a></span>
        </li>`;
      }).join('');
      return `<div class="mb-3">
        <div class="text-xs font-semibold text-gray-600 mb-1">${LABEL[t] || t}
          <span class="text-gray-400 font-normal">· ${groups[t].length}</span></div>
        <ul class="rounded border border-gray-200 px-3 py-1 bg-white">${rows}</ul>
      </div>`;
    };
    return types.map(section).join('');
  }

  // "In their own words" — extractive narrative pulled verbatim from the company's
  // own SEC filings (latest 10-K/20-F + earnings release). `rec` is the payload
  // from /api/filing-insights/{ticker}. No LLM: everything links back to EDGAR.
  function filingInsightsBlock(rec) {
    if (!rec || !rec.exists) {
      return `<div class="text-sm text-gray-500">No filing narrative extracted yet. Run
        <code class="text-xs bg-gray-100 px-1 rounded">python -m backend.filing_insights refresh ${''}&lt;ticker&gt;</code>.
        Companies that don't file with the SEC (Samsung, SK hynix) have none.</div>`;
    }
    const c = rec;
    const parts = [];

    if (c.self_description) {
      parts.push(`<blockquote class="border-l-2 border-gray-800 pl-3 py-1 mb-3 text-[15px] text-gray-900 leading-relaxed font-medium">
        ${escapeHtml(c.self_description)}</blockquote>`);
    }
    if (c.business_overview) {
      parts.push(`<div class="text-sm text-gray-700 leading-relaxed mb-3">${escapeHtml(c.business_overview)}</div>`);
    }
    if (c.segments_note) {
      parts.push(`<div class="border border-gray-200 rounded-lg px-4 py-3 mb-3 bg-white">
        <div class="text-[10px] uppercase tracking-widest text-gray-500 mb-1">Reportable segments</div>
        <div class="text-sm text-gray-800 leading-relaxed">${escapeHtml(c.segments_note)}</div>
      </div>`);
    }
    if (c.strategy_points && c.strategy_points.length) {
      parts.push(`<div class="border border-gray-200 rounded-lg px-4 py-3 mb-3 bg-white">
        <div class="text-[10px] uppercase tracking-widest text-gray-500 mb-2">Strategy, in their words</div>
        <ul class="text-sm text-gray-800 space-y-1">
          ${c.strategy_points.map(s => `<li class="flex gap-2"><span class="text-gray-400">•</span><span>${escapeHtml(s)}</span></li>`).join('')}
        </ul></div>`);
    }
    if (c.mgmt_quotes && c.mgmt_quotes.length) {
      const src = c.mgmt_quotes_source
        ? `<a href="${escapeHtml(c.mgmt_quotes_source)}" target="_blank" rel="noopener" class="text-blue-600 hover:underline">earnings release ↗</a>`
        : 'latest earnings release';
      parts.push(`<div class="mb-2">
        <div class="text-[10px] uppercase tracking-widest text-gray-500 mb-2">Management on the latest quarter · from ${src}</div>
        <div class="space-y-2">
          ${c.mgmt_quotes.map(q => `<blockquote class="border-l-2 border-blue-300 pl-3 py-1 text-sm text-gray-800 leading-relaxed">
            “${escapeHtml(q.quote)}”${q.speaker ? `<div class="text-xs text-gray-500 mt-0.5">— ${escapeHtml(q.speaker)}</div>` : ''}
          </blockquote>`).join('')}
        </div></div>`);
    }
    if (!parts.length) {
      return `<div class="text-sm text-gray-500">No narrative could be extracted from this filer's documents.</div>`;
    }

    const srcBits = [];
    if (c.source_form) srcBits.push(escapeHtml(c.source_form) + (c.source_period ? ' (FY ' + escapeHtml(c.source_period) + ')' : ''));
    const srcLink = c.source_url
      ? `<a href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener" class="text-blue-600 hover:underline">view on SEC ↗</a>` : '';
    const footer = `<div class="text-[11px] text-gray-400 mt-2">Verbatim from ${srcBits.join(' ') || 'SEC filings'}${srcLink ? ' · ' + srcLink : ''} · extracted text, not AI-generated.</div>`;

    return `<div>${parts.join('')}${footer}</div>`;
  }

  // Full company view: price snapshot + derived metrics + LLM insight panel + filings.
  // opts: { ticker, snapshot, heatRow, primaryBenchmark, metrics, insight, filingInsights, filings, notes, compact }
  function renderCompany(target, opts) {
    opts = opts || {};
    const trendsHtml = fundamentalsBlock(opts.fundamentals);
    target.innerHTML = `
      <div data-snap></div>
      <div data-metrics class="mt-5"></div>
      ${trendsHtml ? `<div data-trends class="mt-5">
        <div class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Fundamentals trend</div>
        <div data-trends-body></div>
      </div>` : ''}
      <div data-own-words class="mt-5">
        <div class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">In their own words</div>
        <div data-own-words-body></div>
      </div>
      <div data-insight class="mt-5">
        <div class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">AI deep-dive</div>
        <div data-insight-body></div>
      </div>
      <div data-filings class="mt-5">
        <div class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">SEC filings</div>
        <div data-filings-body></div>
      </div>`;
    renderSnapshot(target.querySelector('[data-snap]'), opts);
    target.querySelector('[data-metrics]').innerHTML = metricsTiles(opts.metrics);
    if (trendsHtml) target.querySelector('[data-trends-body]').innerHTML = trendsHtml;
    target.querySelector('[data-own-words-body]').innerHTML = filingInsightsBlock(opts.filingInsights);
    const ib = target.querySelector('[data-insight-body]');
    ib.innerHTML = insightBlock(target, opts.insight);
    wireHorizonTabs(ib);
    target.querySelector('[data-filings-body]').innerHTML = filingsBlock(opts.filings);
  }

  global.PanelLib = {
    escapeHtml, fmtPct, fmtMoney, apiUrl, niceTicks, priceChartSVG,
    renderSnapshot, renderCompany,
  };
})(window);
