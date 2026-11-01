/* ═══════════════════════════════════════════════════
   advertising.js — Chart.js для дашборду реклами
═══════════════════════════════════════════════════ */

const AD_CHARTS = [];
let periodPickerAbort = null;
let compareToggleAbort = null;
let chartGranularityAbort = null;

var AD_DASH_PREFIXES = [
  '/clients/marketing-report',
  '/clients/ad-analytics',
  '/clients/advertising',
];

function isAdDashPath(pathname) {
  for (var i = 0; i < AD_DASH_PREFIXES.length; i++) {
    var prefix = AD_DASH_PREFIXES[i];
    if (pathname === prefix || pathname.indexOf(prefix + '/') === 0) return true;
  }
  return false;
}

function isAdDashHref(href) {
  for (var j = 0; j < AD_DASH_PREFIXES.length; j++) {
    if (href.indexOf(AD_DASH_PREFIXES[j]) === 0) return true;
  }
  return false;
}

function adDashSectionKey(pathname) {
  for (var k = 0; k < AD_DASH_PREFIXES.length; k++) {
    var p = AD_DASH_PREFIXES[k];
    if (pathname === p) return 'overview';
    if (pathname.indexOf(p + '/') === 0) return pathname.slice(p.length + 1).split('/')[0] || 'overview';
  }
  return '';
}

document.addEventListener('DOMContentLoaded', () => {
  try {
    if (typeof Chart !== 'undefined') {
      Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
      Chart.defaults.color = '#64748b';
      Chart.defaults.borderColor = '#e2e8f0';
      Chart.defaults.plugins.legend.labels.boxWidth = 10;
      Chart.defaults.plugins.legend.labels.boxHeight = 10;
    }
    initSeoExpenses();
    initPeriodPicker();
    initMainChart();
    initMiniCharts();
    initDonut();
    initTableSort();
    initTableSearch();
    initAdSpaNav();
    initCompareToggle();
    initChartGranularity();
    initIntegrationSettings();
    initMetricHelp();
  } catch (err) {
    if (typeof console !== 'undefined' && console.error) {
      console.error('advertising.js init', err);
    }
  }
});

function trackChart(chart) {
  if (chart) AD_CHARTS.push(chart);
  return chart;
}

function parseIsoLocalDate(iso) {
  if (!iso) return null;
  const parts = iso.split('-').map((x) => parseInt(x, 10));
  if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null;
  return new Date(parts[0], parts[1] - 1, parts[2], 12, 0, 0);
}

function formatDisplayDateLocal(d) {
  const day = String(d.getDate()).padStart(2, '0');
  const m = String(d.getMonth() + 1).padStart(2, '0');
  return `${day}.${m}.${d.getFullYear()}`;
}

function shiftYearLocal(d, years) {
  const nd = new Date(d.getTime());
  const month = nd.getMonth();
  nd.setFullYear(nd.getFullYear() + years);
  if (nd.getMonth() !== month) nd.setDate(0);
  return nd;
}

function computeCompareRangeDisplay(fromIso, toIso, mode) {
  const start = parseIsoLocalDate(fromIso);
  const end = parseIsoLocalDate(toIso);
  if (!start || !end || end < start) return '';
  const span = Math.round((end - start) / 86400000) + 1;
  let prevStart;
  let prevEnd;
  if (mode === 'yoy') {
    prevStart = shiftYearLocal(start, -1);
    prevEnd = shiftYearLocal(end, -1);
  } else {
    prevEnd = new Date(start.getTime());
    prevEnd.setDate(prevEnd.getDate() - 1);
    prevStart = new Date(prevEnd.getTime());
    prevStart.setDate(prevStart.getDate() - (span - 1));
  }
  return `${formatDisplayDateLocal(prevStart)} — ${formatDisplayDateLocal(prevEnd)}`;
}

function updateCompareRangeHint(form) {
  if (!form) return;
  const rangeEl = form.querySelector('[data-compare-range]');
  const toolbar = form.querySelector('.mkt-toolbar') || form.closest('.mkt-toolbar');
  const pillEl = toolbar ? toolbar.querySelector('[data-compare-range-pill]') : null;
  const fromPop = form.querySelector('[data-period-from]');
  const toPop = form.querySelector('[data-period-to]');
  const fromHidden = form.querySelector('input[name="date_from"]');
  const toHidden = form.querySelector('input[name="date_to"]');
  const compareInput = form.querySelector('[data-compare-input]');
  const from = (fromPop && fromPop.value) || (fromHidden && fromHidden.value);
  const to = (toPop && toPop.value) || (toHidden && toHidden.value);
  const mode = (compareInput && compareInput.value) || 'prev';
  const range = from && to ? computeCompareRangeDisplay(from, to, mode) : '';
  const kind = mode === 'yoy' ? 'vs аналогічний період минулого року' : 'vs попередній період';
  if (rangeEl) {
    if (!range) {
      rangeEl.textContent = '';
      return;
    }
    rangeEl.innerHTML = `Δ порівнюються з: <strong>${range}</strong> <span class="mkt-muted">(${kind})</span>`;
  }
  if (pillEl) {
    if (!range) {
      pillEl.hidden = true;
      pillEl.textContent = '';
    } else {
      pillEl.hidden = false;
      pillEl.textContent = `Δ ${range}`;
    }
  }
}

function initPeriodPicker() {
  if (typeof AbortController === 'undefined') return;
  if (periodPickerAbort) periodPickerAbort.abort();
  periodPickerAbort = new AbortController();
  const { signal } = periodPickerAbort;

  const pill = document.getElementById('mktPeriodPill');
  const pop = document.getElementById('mktPeriodPopover');
  const form = pill ? pill.closest('form') : null;
  if (!pill || !pop || !form) return;

  const fromHidden = form.querySelector('input[name="date_from"]');
  const toHidden = form.querySelector('input[name="date_to"]');
  const fromPop = pop.querySelector('[data-period-from]');
  const toPop = pop.querySelector('[data-period-to]');
  const maxDate = (fromPop && fromPop.max) || (toPop && toPop.max) || '';

  function openPopover() {
    pop.hidden = false;
    pill.setAttribute('aria-expanded', 'true');
  }
  function closePopover() {
    pop.hidden = true;
    pill.setAttribute('aria-expanded', 'false');
  }
  function togglePopover() {
    if (pop.hidden) openPopover();
    else closePopover();
  }

  pill.addEventListener('click', (e) => {
    e.stopPropagation();
    togglePopover();
  }, { signal });

  document.addEventListener('click', (e) => {
    if (!pop.contains(e.target) && e.target !== pill && !pill.contains(e.target)) {
      closePopover();
    }
  }, { signal });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !pop.hidden) closePopover();
  }, { signal });

  function formatLocalDateInput(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function applyRange(from, to) {
    if (fromPop) fromPop.value = formatLocalDateInput(from);
    if (toPop) toPop.value = formatLocalDateInput(to);
  }

  pop.querySelectorAll('.mkt-period-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      const to = maxDate ? new Date(maxDate + 'T12:00:00') : new Date();
      let from = new Date(to);

      if (btn.dataset.preset === 'month') {
        from = new Date(to.getFullYear(), to.getMonth(), 1);
      } else {
        const days = parseInt(btn.dataset.days || '30', 10);
        from.setDate(from.getDate() - days + 1);
      }

      applyRange(from, to);
      pop.querySelectorAll('.mkt-period-preset').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      updateCompareRangeHint(form);
    }, { signal });
  });

  [fromPop, toPop].forEach((el) => {
    if (!el) return;
    el.addEventListener('change', () => updateCompareRangeHint(form), { signal });
    el.addEventListener('input', () => updateCompareRangeHint(form), { signal });
  });

  updateCompareRangeHint(form);

  form.addEventListener('submit', (e) => {
    if (fromPop && fromHidden) fromHidden.value = fromPop.value;
    if (toPop && toHidden) toHidden.value = toPop.value;

    if (!isAdDashPath(window.location.pathname) || typeof loadAdPartial !== 'function') return;

    e.preventDefault();
    const params = new URLSearchParams(new FormData(form));
    const submitter = e.submitter;
    if (submitter && submitter.name === 'refresh') {
      params.set('refresh', submitter.value || '1');
    } else {
      params.delete('refresh');
    }
    const sectionPath = isAdDashPath(window.location.pathname)
      ? window.location.pathname
      : (form.getAttribute('action') || window.location.pathname);
    const href = sectionPath + (params.toString() ? '?' + params.toString() : '');
    loadAdPartial(href, { push: true });
  }, { signal });
}

function initMiniCharts() {
  document.querySelectorAll('canvas.mkt-mini-chart[data-chart]').forEach(canvas => {
    let chartData = [];
    try { chartData = JSON.parse(canvas.dataset.chart || '[]'); } catch (_) { return; }
    if (!chartData.length) return;

    const labels = chartData.map(d => fmtDate(d.date));
    trackChart(new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Трафік',
          data: chartData.map(d => d.clicks || 0),
          borderColor: '#34A853',
          backgroundColor: 'rgba(52, 168, 83, 0.12)',
          fill: true,
          tension: 0.4,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 2,
        }],
      },
      options: miniChartOptions(),
    }));
  });
}

function miniChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: { legend: { display: false }, tooltip: baseTooltip() },
    scales: {
      x: { display: false },
      y: { display: false },
    },
  };
}

function initMainChart() {
  const canvas = document.getElementById('mktMainChart');
  if (!canvas) return;

  let chartData = [];
  try { chartData = JSON.parse(canvas.dataset.chart || '[]'); } catch (_) { return; }
  if (!chartData.length) return;

  const mode = canvas.dataset.mode || 'default';
  const labels = chartData.map(d => fmtDate(d.date));
  let datasets = [];

  if (mode === 'overview') {
    datasets = [
      {
        label: 'Витрати',
        data: chartData.map(d => d.spend || 0),
        borderColor: '#F97316',
        backgroundColor: 'rgba(249, 115, 22, 0.14)',
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2,
        yAxisID: 'yLeft',
      },
      {
        label: 'Дохід',
        data: chartData.map(d => d.revenue || 0),
        borderColor: '#3B82F6',
        backgroundColor: 'rgba(59, 130, 246, 0.06)',
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2.5,
        yAxisID: 'yLeft',
      },
      {
        label: 'Замовлення',
        data: chartData.map(d => d.orders || 0),
        borderColor: '#8B5CF6',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
        borderDash: [5, 4],
        yAxisID: 'yRight',
      },
    ];
    trackChart(renderDualAxisChart(canvas, labels, datasets, { moneyLeft: true, countRight: true }));
    return;
  }

  if (mode === 'seo') {
    datasets = [
      {
        label: 'Органічний трафік',
        data: chartData.map(d => d.clicks || 0),
        borderColor: '#34A853',
        backgroundColor: 'rgba(52, 168, 83, 0.1)',
        fill: true,
        tension: 0.4,
        yAxisID: 'yLeft',
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      },
      {
        label: 'Дохід CRM',
        data: chartData.map(d => d.revenue || 0),
        borderColor: '#4285F4',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0.4,
        yAxisID: 'yRight',
        borderDash: [5, 4],
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      },
    ];
    trackChart(renderDualAxisChart(canvas, labels, datasets, { countLeft: true, moneyRight: true }));
    return;
  }

  if (mode === 'direct') {
    const hasSessions = chartData.some(d => (d.sessions || 0) > 0);
    datasets = [
      {
        label: hasSessions ? 'Сесії' : 'Замовлення',
        data: chartData.map(d => hasSessions ? (d.sessions || 0) : (d.orders || 0)),
        borderColor: '#6366F1',
        backgroundColor: 'rgba(99, 102, 241, 0.12)',
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2,
        yAxisID: 'yLeft',
      },
      {
        label: 'Дохід',
        data: chartData.map(d => d.revenue || 0),
        borderColor: '#3B82F6',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 5,
        borderWidth: 2.5,
        yAxisID: 'yRight',
      },
    ];
    trackChart(renderDualAxisChart(canvas, labels, datasets, { countLeft: true, moneyRight: true }));
    return;
  }

  datasets = [
    {
      label: 'Витрати',
      data: chartData.map(d => d.spend || 0),
      borderColor: '#F97316',
      backgroundColor: 'rgba(249, 115, 22, 0.12)',
      fill: true,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 2,
      yAxisID: 'yLeft',
    },
    {
      label: 'Дохід',
      data: chartData.map(d => d.revenue || 0),
      borderColor: '#3B82F6',
      backgroundColor: 'transparent',
      fill: false,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 2.5,
      yAxisID: 'yLeft',
    },
    {
      label: 'ROAS',
      data: chartData.map(d => (d.roas != null ? d.roas : null)),
      borderColor: '#22C55E',
      backgroundColor: 'transparent',
      fill: false,
      tension: 0.35,
      borderDash: [5, 4],
      pointRadius: 0,
      borderWidth: 2,
      yAxisID: 'yRight',
    },
  ];
  trackChart(renderDualAxisChart(canvas, labels, datasets, { moneyLeft: true, roasRight: true }));
}

function baseTooltip() {
  return {
    backgroundColor: 'rgba(255, 255, 255, 0.98)',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    titleColor: '#0f172a',
    bodyColor: '#334155',
    titleFont: { size: 12, weight: '600' },
    bodyFont: { size: 12 },
    padding: 12,
    boxPadding: 6,
    usePointStyle: true,
    callbacks: {
      label: ctx => {
        const v = ctx.parsed.y;
        if (v === null || v === undefined) return null;
        const suffix = (ctx.dataset.label === 'Дохід' || ctx.dataset.label === 'Витрати') ? ' ₴' : '';
        return `${ctx.dataset.label}: ${fmtNum(v)}${suffix}`;
      },
    },
  };
}

function renderDualAxisChart(canvas, labels, datasets, opts = {}) {
  const hasRight = datasets.some(d => d.yAxisID === 'yRight');

  datasets.forEach(d => {
    if (!d.yAxisID) d.yAxisID = 'yLeft';
  });

  return new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500, easing: 'easeOutQuart' },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          align: 'start',
          labels: {
            usePointStyle: true,
            pointStyle: 'circle',
            font: { size: 11, weight: '500' },
            color: '#64748b',
            padding: 16,
          },
        },
        tooltip: baseTooltip(),
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: '#94a3b8',
            font: { size: 11 },
            maxTicksLimit: 8,
            maxRotation: 0,
          },
        },
        yLeft: {
          type: 'linear',
          position: 'left',
          beginAtZero: true,
          grace: '8%',
          grid: { color: '#f1f5f9', drawBorder: false },
          border: { display: false },
          ticks: {
            color: '#94a3b8',
            font: { size: 10 },
            padding: 8,
            maxTicksLimit: 6,
            callback: v => opts.moneyLeft ? fmtMoneyAxis(v) : fmtNum(v),
          },
        },
        ...(hasRight ? {
          yRight: {
            type: 'linear',
            position: 'right',
            beginAtZero: true,
            grace: '8%',
            grid: { drawOnChartArea: false },
            border: { display: false },
            ticks: {
              color: '#94a3b8',
              font: { size: 10 },
              padding: 8,
              maxTicksLimit: 6,
              callback: v => {
                if (opts.moneyRight) return fmtMoneyAxis(v);
                if (opts.roasRight) return fmtNum(v);
                if (opts.positionRight) return fmtNum(v);
                return fmtNum(v);
              },
            },
          },
        } : {}),
      },
    },
  });
}

function initDonut() {
  document.querySelectorAll('canvas[data-donut]').forEach(canvas => {
    let data = {};
    try { data = JSON.parse(canvas.dataset.donut || '{}'); } catch (_) { return; }
    if (!data.labels || !data.labels.length) return;

    const centerLabel = canvas.dataset.centerLabel || '';
    const centerValue = canvas.dataset.centerValue || '';

    trackChart(new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: data.labels,
        datasets: [{
          data: data.values,
          backgroundColor: data.colors || defaultColors(data.labels.length),
          borderWidth: 3,
          borderColor: '#ffffff',
          hoverOffset: 8,
          spacing: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        aspectRatio: 1,
        cutout: '68%',
        layout: { padding: 4 },
        animation: { animateRotate: true, duration: 600 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#fff',
            borderColor: '#e2e8f0',
            borderWidth: 1,
            titleColor: '#0f172a',
            bodyColor: '#334155',
            padding: 10,
            callbacks: {
              label: ctx => {
                const total = ctx.chart.data.datasets[0].data.reduce((a, b) => a + b, 0);
                const pct = total ? Math.round(ctx.parsed / total * 100) : 0;
                const unit = (data.unit || 'uah');
                const suffix = unit === 'uah' ? ' ₴' : (unit === 'sessions' ? ' сес.' : (unit === 'clicks' ? ' клік.' : ''));
                return `${ctx.label}: ${fmtNum(ctx.parsed)}${suffix} (${pct}%)`;
              },
            },
          },
        },
      },
      plugins: [{
        id: 'centerText',
        afterDraw(chart) {
          const label = centerLabel;
          const value = centerValue;
          if (!label && !value) return;
          const { ctx, chartArea: { left, top, right, bottom } } = chart;
          const cx = (left + right) / 2;
          const cy = (top + bottom) / 2;
          ctx.save();
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          if (value) {
            ctx.fillStyle = '#0f172a';
            ctx.font = '600 13px Inter, sans-serif';
            ctx.fillText(value, cx, cy - (label ? 8 : 0));
          }
          if (label) {
            ctx.fillStyle = '#94a3b8';
            ctx.font = '500 10px Inter, sans-serif';
            ctx.fillText(label, cx, cy + (value ? 10 : 0));
          }
          ctx.restore();
        },
      }],
    }));
  });

  const devCanvas = document.querySelector('canvas[data-mode="devices"]');
  if (devCanvas) {
    let devices = [];
    try { devices = JSON.parse(devCanvas.dataset.devices || '[]'); } catch (_) { devices = []; }
    let labels, values;
    if (devices.length) {
      labels = devices.map(d => d.device);
      values = devices.map(d => d.sessions);
    } else {
      const orders = parseInt(devCanvas.dataset.orders || '0', 10);
      labels = ['Desktop', 'Mobile', 'Tablet'];
      values = [Math.round(orders * 0.6), Math.round(orders * 0.35), orders - Math.round(orders * 0.6) - Math.round(orders * 0.35)];
    }
    trackChart(new Chart(devCanvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: ['#3B82F6', '#60A5FA', '#93C5FD'],
          borderRadius: 8,
          barPercentage: 0.55,
          borderSkipped: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#fff',
            borderColor: '#e2e8f0',
            borderWidth: 1,
            callbacks: { label: ctx => `Сесії: ${fmtNum(ctx.parsed.y)}` },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 12 } } },
          y: { grid: { color: '#f1f5f9' }, border: { display: false }, ticks: { color: '#94a3b8', callback: v => fmtNum(v) } },
        },
      },
    }));
  }
}

function initTableSort() {
  document.querySelectorAll('table[data-sortable]:not([data-sort-bound])').forEach(table => {
    table.dataset.sortBound = '1';
    table.querySelectorAll('thead th').forEach((th, idx) => {
      th.style.cursor = 'pointer';
      th.addEventListener('click', () => sortTable(table, idx, th));
    });
  });
}

function sortTable(table, colIdx, th) {
  const asc = th.classList.contains('sort-asc');
  table.querySelectorAll('thead th').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
  th.classList.add(asc ? 'sort-desc' : 'sort-asc');
  const dir = asc ? -1 : 1;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {
    const ca = a.cells[colIdx];
    const cb = b.cells[colIdx];
    const va = ca ? (ca.dataset.value !== undefined ? parseFloat(ca.dataset.value) || 0 : ca.textContent.trim()) : '';
    const vb = cb ? (cb.dataset.value !== undefined ? parseFloat(cb.dataset.value) || 0 : cb.textContent.trim()) : '';
    if (typeof va === 'number') return dir * (va - vb);
    return dir * String(va).localeCompare(String(vb), 'uk');
  });
  rows.forEach(r => tbody.appendChild(r));
}

function initTableSearch() {
  document.querySelectorAll('.mkt-card').forEach(card => {
    const table = card.querySelector('table[data-sortable]');
    if (!table) return;

    let input = card.querySelector('.mkt-table-search');
    const head = card.querySelector('.mkt-card__head');
    const countEl = card.querySelector('[data-table-count]');

    if (!input && head) {
      head.classList.add('mkt-card__head--table');
      const headLeft = head.querySelector('.mkt-card__head-left');
      if (!headLeft && head.querySelector('.mkt-card__title')) {
        const title = head.querySelector('.mkt-card__title');
        const wrap = document.createElement('div');
        wrap.className = 'mkt-card__head-left';
        const cnt = document.createElement('span');
        cnt.className = 'mkt-table-count';
        cnt.dataset.tableCount = '';
        wrap.appendChild(title);
        wrap.appendChild(cnt);
        head.insertBefore(wrap, head.firstChild);
      }
      input = document.createElement('input');
      input.type = 'search';
      input.className = 'mkt-table-search';
      input.placeholder = 'Пошук у таблиці…';
      input.setAttribute('aria-label', 'Пошук у таблиці');
      head.appendChild(input);
    }

    if (!input) return;

    const countBadge = countEl || card.querySelector('[data-table-count]');
    const rows = () => Array.from(table.querySelectorAll('tbody tr'));

    function updateCount(visible, total) {
      if (!countBadge) return;
      countBadge.textContent = visible === total ? `${total} рядків` : `${visible} з ${total}`;
    }

    function filterRows() {
      const q = input.value.trim().toLowerCase();
      const all = rows();
      let visible = 0;
      all.forEach(row => {
        const match = !q || row.textContent.toLowerCase().includes(q);
        row.classList.toggle('is-hidden', !match);
        if (match) visible++;
      });
      updateCount(visible, all.length);
    }

    updateCount(rows().length, rows().length);
    input.addEventListener('input', filterRows);
  });
}

function initCompareToggle() {
  if (typeof AbortController === 'undefined') return;
  if (compareToggleAbort) compareToggleAbort.abort();
  compareToggleAbort = new AbortController();
  const { signal } = compareToggleAbort;

  const form = document.querySelector('[data-mkt-period-form]');
  const compareInput = form ? form.querySelector('[data-compare-input]') : null;
  if (!form || !compareInput) return;

  form.querySelectorAll('.mkt-period-compare-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      compareInput.value = btn.dataset.compare || 'prev';
      form.querySelectorAll('.mkt-period-compare-btn').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      const fromPop = form.querySelector('[data-period-from]');
      const toPop = form.querySelector('[data-period-to]');
      const fromHidden = form.querySelector('input[name="date_from"]');
      const toHidden = form.querySelector('input[name="date_to"]');
      if (fromPop && fromHidden) fromHidden.value = fromPop.value;
      if (toPop && toHidden) toHidden.value = toPop.value;
      updateCompareRangeHint(form);
      if (!isAdDashPath(window.location.pathname) || typeof loadAdPartial !== 'function') return;
      const params = new URLSearchParams(new FormData(form));
      params.set('compare', compareInput.value);
      const href = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
      loadAdPartial(href, { push: true });
    }, { signal });
  });
}

function destroyAdCharts() {
  while (AD_CHARTS.length) {
    const ch = AD_CHARTS.pop();
    try { ch.destroy(); } catch (_) { /* ignore */ }
  }
}

function reinitAdPageScripts() {
  initSeoExpenses();
  try {
    destroyAdCharts();
    initPeriodPicker();
    initMainChart();
    initMiniCharts();
    initDonut();
    initTableSort();
    initTableSearch();
    initCompareToggle();
    initChartGranularity();
    initIntegrationSettings();
    initMetricHelp();
  } catch (err) {
    if (typeof console !== 'undefined' && console.error) {
      console.error('advertising.js reinit', err);
    }
  }
}

let seoExpensesBound = false;

function seoExpenseCsrfToken() {
  const mktWrap = document.querySelector('.mkt-wrap');
  return (mktWrap && mktWrap.getAttribute('data-csrf')) || '';
}

function seoExpenseShowError(msg) {
  const errEl = document.getElementById('mktSeoExpenseError');
  if (!errEl) return;
  if (msg) {
    errEl.textContent = msg;
    errEl.hidden = false;
  } else {
    errEl.textContent = '';
    errEl.hidden = true;
  }
}

function seoExpenseResetForm() {
  const form = document.getElementById('mktSeoExpenseForm');
  if (!form) return;
  form.reset();
  const idInput = document.getElementById('mktSeoExpenseId');
  const submitBtn = document.getElementById('mktSeoExpenseSubmit');
  const cancelBtn = document.getElementById('mktSeoExpenseCancel');
  if (idInput) idInput.value = '';
  if (submitBtn) submitBtn.textContent = 'Додати витрату';
  if (cancelBtn) cancelBtn.hidden = true;
  seoExpenseShowError('');
}

function seoExpenseReloadSection() {
  const path = window.location.pathname + window.location.search;
  if (typeof loadAdPartial === 'function') {
    loadAdPartial(path, { push: false, replace: true });
  } else {
    window.location.reload();
  }
}

async function seoExpensePost(url, fields) {
  const fd = new FormData();
  fd.append('csrf_token', seoExpenseCsrfToken());
  Object.keys(fields).forEach((key) => {
    fd.append(key, fields[key]);
  });
  const res = await fetch(url, {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    const msg = data.error || data.detail || 'Не вдалося зберегти витрату';
    throw new Error(typeof msg === 'string' ? msg : 'Не вдалося зберегти витрату');
  }
  return data;
}

function initSeoExpenses() {
  if (seoExpensesBound) return;
  seoExpensesBound = true;

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form || form.id !== 'mktSeoExpenseForm') return;
    e.preventDefault();

    const idInput = document.getElementById('mktSeoExpenseId');
    const dateInput = document.getElementById('mktSeoExpenseDate');
    const amountInput = document.getElementById('mktSeoExpenseAmount');
    const descInput = document.getElementById('mktSeoExpenseDescription');
    const submitBtn = document.getElementById('mktSeoExpenseSubmit');

    seoExpenseShowError('');
    const expenseId = (idInput && idInput.value) || '';
    const payload = {
      expense_date: dateInput ? dateInput.value : '',
      amount_uah: amountInput ? amountInput.value : '',
      description: descInput ? descInput.value : '',
    };
    try {
      if (submitBtn) submitBtn.disabled = true;
      if (expenseId) {
        await seoExpensePost(`/mkt-report/seo-expenses/${encodeURIComponent(expenseId)}/update`, payload);
      } else {
        await seoExpensePost('/mkt-report/seo-expenses', payload);
      }
      seoExpenseResetForm();
      seoExpenseReloadSection();
    } catch (exc) {
      seoExpenseShowError(exc.message || String(exc));
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  document.addEventListener('click', async (e) => {
    const wrap = document.getElementById('mktSeoExpenses');
    if (!wrap || !wrap.contains(e.target)) return;

    const cancelBtn = e.target.closest('#mktSeoExpenseCancel');
    if (cancelBtn) {
      seoExpenseResetForm();
      return;
    }

    const editBtn = e.target.closest('.mkt-seo-expense-edit');
    if (editBtn) {
      const idInput = document.getElementById('mktSeoExpenseId');
      const dateInput = document.getElementById('mktSeoExpenseDate');
      const amountInput = document.getElementById('mktSeoExpenseAmount');
      const descInput = document.getElementById('mktSeoExpenseDescription');
      const submitBtn = document.getElementById('mktSeoExpenseSubmit');
      const cancelEl = document.getElementById('mktSeoExpenseCancel');
      if (idInput) idInput.value = editBtn.getAttribute('data-id') || '';
      if (dateInput) dateInput.value = editBtn.getAttribute('data-date') || '';
      if (amountInput) amountInput.value = editBtn.getAttribute('data-amount') || '';
      if (descInput) descInput.value = editBtn.getAttribute('data-description') || '';
      if (submitBtn) submitBtn.textContent = 'Зберегти зміни';
      if (cancelEl) cancelEl.hidden = false;
      seoExpenseShowError('');
      if (dateInput) dateInput.focus();
      return;
    }

    const delBtn = e.target.closest('.mkt-seo-expense-delete');
    if (!delBtn) return;
    const expenseId = delBtn.getAttribute('data-id');
    if (!expenseId) return;
    if (!window.confirm('Видалити цю витрату?')) return;
    try {
      delBtn.disabled = true;
      await seoExpensePost(`/mkt-report/seo-expenses/${encodeURIComponent(expenseId)}/delete`, {});
      seoExpenseReloadSection();
    } catch (exc) {
      seoExpenseShowError(exc.message || String(exc));
      delBtn.disabled = false;
    }
  });
}

let metricHelpBound = false;
let metricHelpFloat = null;
let metricHelpAnchor = null;

function ensureMetricHelpFloat() {
  if (metricHelpFloat && document.body.contains(metricHelpFloat)) return metricHelpFloat;
  const el = document.createElement('div');
  el.id = 'mktMetricHelpFloat';
  el.className = 'mkt-metric-help-float';
  el.setAttribute('role', 'tooltip');
  el.hidden = true;
  document.body.appendChild(el);
  metricHelpFloat = el;
  return el;
}

function hideMetricHelpFloat({ keepOpen = false } = {}) {
  const el = metricHelpFloat;
  if (el) {
    el.hidden = true;
    el.textContent = '';
  }
  if (!keepOpen) {
    document.querySelectorAll('.mkt-metric-help.is-open').forEach((node) => node.classList.remove('is-open'));
    metricHelpAnchor = null;
  }
}

function placeMetricHelpFloat(help) {
  const source = help.querySelector('.mkt-metric-help__tip');
  const text = (source && source.textContent || '').trim();
  if (!text) return;
  const el = ensureMetricHelpFloat();
  el.textContent = text;
  el.hidden = false;
  el.style.left = '0px';
  el.style.top = '0px';
  const icon = help.querySelector('.mkt-metric-help__icon') || help;
  const r = icon.getBoundingClientRect();
  const tw = el.offsetWidth;
  const th = el.offsetHeight;
  const pad = 8;
  let left = r.left + r.width / 2 - tw / 2;
  left = Math.max(pad, Math.min(left, window.innerWidth - tw - pad));
  let top = r.top - th - pad;
  let placement = 'above';
  if (top < pad) {
    top = r.bottom + pad;
    placement = 'below';
  }
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
  el.dataset.placement = placement;
  metricHelpAnchor = help;
}

function initMetricHelp() {
  if (metricHelpBound) return;
  metricHelpBound = true;
  ensureMetricHelpFloat();

  document.addEventListener('pointerover', (e) => {
    const help = e.target.closest('.mkt-metric-help');
    if (!help) return;
    const from = e.relatedTarget;
    if (from && help.contains(from)) return;
    placeMetricHelpFloat(help);
  });

  document.addEventListener('pointerout', (e) => {
    const help = e.target.closest('.mkt-metric-help');
    if (!help) return;
    const to = e.relatedTarget;
    if (to && help.contains(to)) return;
    if (help.classList.contains('is-open')) return;
    if (metricHelpAnchor === help) hideMetricHelpFloat();
  });

  document.addEventListener('focusin', (e) => {
    const help = e.target.closest('.mkt-metric-help');
    if (help) placeMetricHelpFloat(help);
  });

  document.addEventListener('focusout', (e) => {
    const help = e.target.closest('.mkt-metric-help');
    if (!help || help.classList.contains('is-open')) return;
    if (metricHelpAnchor === help) hideMetricHelpFloat();
  });

  document.addEventListener('click', (e) => {
    const help = e.target.closest('.mkt-metric-help');
    if (help) {
      e.stopPropagation();
      const wasOpen = help.classList.contains('is-open');
      document.querySelectorAll('.mkt-metric-help.is-open').forEach((el) => el.classList.remove('is-open'));
      if (!wasOpen) {
        help.classList.add('is-open');
        placeMetricHelpFloat(help);
      } else {
        hideMetricHelpFloat();
      }
      return;
    }
    hideMetricHelpFloat();
  });

  window.addEventListener('scroll', () => {
    if (metricHelpAnchor) placeMetricHelpFloat(metricHelpAnchor);
  }, true);
  window.addEventListener('resize', () => hideMetricHelpFloat());
}

function initChartGranularity() {
  if (typeof AbortController === 'undefined') return;
  if (chartGranularityAbort) chartGranularityAbort.abort();
  chartGranularityAbort = new AbortController();
  const { signal } = chartGranularityAbort;

  const select = document.querySelector('[data-chart-granularity]');
  const form = document.querySelector('[data-mkt-period-form]');
  if (!select || !form) return;

  select.addEventListener('change', () => {
    const input = form.querySelector('[data-chart-granularity-input]');
    if (input) input.value = select.value;
    const params = new URLSearchParams(window.location.search);
    params.set('chart_granularity', select.value);
    const href = `${window.location.pathname}?${params.toString()}`;
    if (typeof loadAdPartial === 'function' && document.querySelector('.mkt-wrap')) {
      loadAdPartial(href, { push: true });
      return;
    }
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.submit();
  }, { signal });
}

function adMergePeriodParams(href) {
  const target = new URL(href, window.location.origin);
  const current = new URLSearchParams(window.location.search);
  ['date_from', 'date_to', 'compare', 'chart_granularity'].forEach((key) => {
    const val = current.get(key);
    if (val && !target.searchParams.has(key)) target.searchParams.set(key, val);
  });
  return target;
}

function setAdNavActive(pathname) {
  var activeSection = adDashSectionKey(pathname);
  document.querySelectorAll('.mkt-section-nav .mkt-nav-link').forEach((a) => {
    const linkPath = (a.getAttribute('href') || '').split('?')[0];
    const active = adDashSectionKey(linkPath) === activeSection;
    a.classList.toggle('is-active', active);
    if (active) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

let adPartialAbort = null;

function syncPeriodToolbarFromDoc(doc) {
  const newForm = doc.querySelector('[data-mkt-period-form]');
  const form = document.querySelector('[data-mkt-period-form]');
  if (!newForm || !form) return;

  form.action = newForm.getAttribute('action') || form.action;

  const copyValue = (selector) => {
    const src = newForm.querySelector(selector);
    const dst = form.querySelector(selector);
    if (src && dst && src.value !== undefined) dst.value = src.value;
  };
  copyValue('input[name="date_from"]');
  copyValue('input[name="date_to"]');
  copyValue('[data-compare-input]');
  copyValue('[data-chart-granularity-input]');
  copyValue('[data-period-from]');
  copyValue('[data-period-to]');

  const newPillText = newForm.querySelector('.mkt-period-pill__text');
  const pillText = form.querySelector('.mkt-period-pill__text');
  if (newPillText && pillText) pillText.textContent = newPillText.textContent;

  const newCompare = newForm.querySelector('.mkt-period-compare');
  const compare = form.querySelector('.mkt-period-compare');
  if (newCompare && compare) compare.innerHTML = newCompare.innerHTML;

  const newRange = newForm.querySelector('[data-compare-range]');
  const range = form.querySelector('[data-compare-range]');
  if (newRange && range) range.innerHTML = newRange.innerHTML;

  const newCp = newForm.querySelector('[data-compare-range-pill]');
  const cp = form.querySelector('[data-compare-range-pill]');
  if (newCp && cp) {
    cp.textContent = newCp.textContent;
    cp.hidden = newCp.hidden;
    if (newCp.title) cp.title = newCp.title;
  }

  const newNote = newForm.querySelector('[data-period-note]');
  const note = form.querySelector('[data-period-note]');
  if (newNote && note) {
    note.innerHTML = newNote.innerHTML;
    note.hidden = newNote.hidden;
  } else if (note && !newNote) {
    note.hidden = true;
    note.textContent = '';
  }
}

function updateAdChromeFromDoc(doc) {
  const newWrap = doc.querySelector('.mkt-wrap');
  if (!newWrap) return;

  const wrap = document.querySelector('.mkt-wrap');
  if (wrap && newWrap.dataset.section) wrap.dataset.section = newWrap.dataset.section;
  if (wrap && newWrap.dataset.csrf) wrap.dataset.csrf = newWrap.dataset.csrf;

  const newTitle = newWrap.querySelector('.mkt-topbar__title');
  const title = document.querySelector('.mkt-topbar__title');
  if (newTitle && title) title.innerHTML = newTitle.innerHTML;

  syncPeriodToolbarFromDoc(doc);

  const newToolbar = newWrap.querySelector('.mkt-toolbar__actions');
  const toolbar = document.querySelector('.mkt-toolbar__actions');
  if (newToolbar && toolbar) {
    toolbar.querySelectorAll('a.mkt-icon-btn').forEach((link, i) => {
      const next = newToolbar.querySelectorAll('a.mkt-icon-btn')[i];
      if (next) link.href = next.href;
    });
  }
}

async function loadAdPartial(href, { push = true, replace = false } = {}) {
  const wrap = document.querySelector('.mkt-wrap');
  const pageBody = document.getElementById('mktPageBody');
  if (!wrap || typeof AbortController === 'undefined' || typeof fetch === 'undefined') {
    window.location.href = href;
    return;
  }

  const target = adMergePeriodParams(href);
  const fetchUrl = target.pathname + target.search + (target.search ? '&' : '?') + 'partial=1';
  const prevPath = window.location.pathname;

  if (adPartialAbort) adPartialAbort.abort();
  adPartialAbort = new AbortController();

  if (typeof hideMetricHelpFloat === 'function') hideMetricHelpFloat();

  if (pageBody) pageBody.classList.add('mkt-page-body--loading');
  const loadingTimer = setTimeout(() => {
    const el = document.getElementById('mktPageBody');
    if (el) el.classList.remove('mkt-page-body--loading');
  }, 20000);
  try {
    const res = await fetch(fetchUrl, {
      headers: { 'X-Requested-With': 'mkt-spa' },
      signal: adPartialAbort.signal,
      cache: 'no-store',
    });
    if (!res.ok) throw new Error('load failed');
    const html = await res.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const newWrap = doc.querySelector('.mkt-wrap');
    if (!newWrap) throw new Error('no content');

    const newBody = newWrap.querySelector('#mktPageBody');
    if (pageBody && newBody) {
      pageBody.innerHTML = newBody.innerHTML;
      updateAdChromeFromDoc(doc);
    } else {
      wrap.replaceWith(newWrap);
      setAdNavActive(target.pathname);
    }

    const displayParams = new URLSearchParams(target.search);
    displayParams.delete('refresh');
    const displayQuery = displayParams.toString();
    const displayUrl = target.pathname + (displayQuery ? '?' + displayQuery : '');
    setAdNavActive(target.pathname);
    if (push) history.pushState({ adSpa: true }, '', displayUrl);
    else if (replace) history.replaceState({ adSpa: true }, '', displayUrl);

    reinitAdPageScripts();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    setAdNavActive(prevPath);
    if (pageBody) {
      pageBody.innerHTML = (
        '<div class="mkt-section"><div class="mkt-alert mkt-alert--auth">'
        + '⚠️ Не вдалося завантажити розділ. '
        + '<button type="button" class="mkt-alert__btn" onclick="window.location.reload()">Оновити</button>'
        + ' або <a href="' + (target.pathname + target.search) + '">відкрити повністю</a>.'
        + '</div></div>'
      );
      return;
    }
    window.location.href = target.pathname + target.search;
  } finally {
    clearTimeout(loadingTimer);
    const el = document.getElementById('mktPageBody');
    if (el) el.classList.remove('mkt-page-body--loading');
  }
}

function initAdSpaNav() {
  if (!document.querySelector('.mkt-wrap')) return;
  if (window.__adSpaNavBound) return;
  window.__adSpaNavBound = true;

  document.addEventListener('click', async (e) => {
    const link = e.target.closest('a.mkt-nav-link, a.mkt-channel-link');
    if (!link || link.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const href = link.getAttribute('href');
    if (!href || !isAdDashHref(href)) return;
    if (href.includes('/export.') || href.includes('sync_crm=1')) return;

    e.preventDefault();
    await loadAdPartial(href, { push: true });
  });

  window.addEventListener('popstate', () => {
    if (!isAdDashPath(window.location.pathname)) return;
    loadAdPartial(window.location.pathname + window.location.search, { push: false, replace: true });
  });
}

function fmtNum(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1000000) return (n / 1000000).toFixed(1) + ' млн';
  if (abs >= 10000) return Math.round(n).toLocaleString('uk-UA');
  if (abs >= 1000) return n.toLocaleString('uk-UA');
  return Number.isInteger(n) ? String(n) : Number(n).toFixed(1);
}

function fmtMoneyAxis(n) {
  if (n === null || n === undefined || isNaN(n)) return '';
  const abs = Math.abs(n);
  if (abs >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (abs >= 1000) return (n / 1000).toFixed(0) + 'k';
  return String(Math.round(n));
}

function fmtDate(iso) {
  if (!iso) return '';
  const monthNames = ['', 'січ', 'лют', 'бер', 'кві', 'тра', 'чер', 'лип', 'сер', 'вер', 'жов', 'лис', 'гру'];
  if (/^\d{4}-\d{2}$/.test(iso)) {
    const [, m] = iso.split('-');
    const y = iso.slice(0, 4);
    return `${monthNames[parseInt(m, 10)]} ${y}`;
  }
  const weekMatch = /^(\d{4})-W(\d{2})$/.exec(iso);
  if (weekMatch) {
    return `тиж. ${weekMatch[2]}, ${weekMatch[1]}`;
  }
  const quarterMatch = /^(\d{4})-Q([1-4])$/.exec(iso);
  if (quarterMatch) {
    return `${quarterMatch[2]} кв. ${quarterMatch[1]}`;
  }
  if (iso.length < 10) return iso;
  const [, m, d] = iso.split('-');
  return `${parseInt(d, 10)} ${monthNames[parseInt(m, 10)]}`;
}

function defaultColors(n) {
  const palette = ['#4285F4', '#34A853', '#F59E0B', '#1877F2', '#94A3B8', '#EA4335', '#8B5CF6', '#14B8A6'];
  return Array.from({ length: n }, (_, i) => palette[i % palette.length]);
}

let integrationSettingsAbort = null;

function initIntegrationSettings() {
  if (integrationSettingsAbort) integrationSettingsAbort.abort();
  integrationSettingsAbort = new AbortController();
  const { signal } = integrationSettingsAbort;

  const wrap = document.querySelector('.mkt-wrap[data-integrations]');
  const dialog = document.getElementById('mktIntegrationDialog');
  const body = document.getElementById('mktIntegrationDialogBody');
  const errEl = document.getElementById('mktIntegrationDialogError');
  const saveBtn = document.getElementById('mktIntegrationSaveBtn');
  const openBtn = document.getElementById('mktIntegrationSettingsBtn');
  if (!wrap || !dialog || !body) return;

  let payload;
  try {
    payload = JSON.parse(wrap.getAttribute('data-integrations') || '{}');
  } catch (_) {
    payload = {};
  }
  const integrations = payload.integrations || [];
  const isAdmin = wrap.getAttribute('data-is-admin') === '1';
  const csrf = wrap.getAttribute('data-csrf') || '';
  let activeKey = (integrations[0] && integrations[0].key) || null;

  const statusLabels = {
    ok: 'OK',
    auth_error: 'Прострочено',
    api_error: 'Помилка API',
    off: 'Не налаштовано',
  };

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function startLocalGoogleOAuth(local, btn) {
    const base = (local && local.daemon_base_url) || 'http://localhost:8765';
    const daemonCmd = (local && local.daemon_command) || 'python scripts/google_ads_oauth_cli.py --daemon';
    const label = btn ? (btn.getAttribute('data-label') || btn.textContent) : 'Підключити через Google';

    const ping = async () => {
      const res = await fetch(`${base}/health`, { mode: 'cors', cache: 'no-store' });
      if (!res.ok) throw new Error('daemon_off');
      return res.json();
    };

    try {
      await ping();
    } catch (_) {
      window.alert(
        `Спочатку запустіть у терміналі (у папці проєкту):\n\n${daemonCmd}\n\nПотім знову натисніть «Підключити через Google».`
      );
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Відкриваю Google…';
    }

    try {
      const startRes = await fetch(`${base}/start`, { mode: 'cors', cache: 'no-store' });
      const startData = await startRes.json().catch(() => ({}));
      if (!startRes.ok) {
        throw new Error(startData.error || 'Не вдалось запустити OAuth');
      }
    } catch (exc) {
      window.alert(`❌ ${exc.message || exc}`);
      if (btn) {
        btn.disabled = false;
        btn.textContent = label;
      }
      return;
    }

    if (btn) btn.textContent = 'Очікую підтвердження Google…';

    for (let i = 0; i < 180; i += 1) {
      await sleep(1000);
      try {
        const stRes = await fetch(`${base}/status`, { mode: 'cors', cache: 'no-store' });
        const st = await stRes.json();
        if (st.state === 'done') {
          const okMsg = st.verified
            ? `✅ Google Ads підключено. ${st.verified}`
            : '✅ Google Ads підключено. Refresh token збережено в marketing_config.json';
          window.alert(okMsg);
          window.location.reload();
          return;
        }
        if (st.state === 'error') {
          throw new Error(st.error || 'Помилка OAuth');
        }
      } catch (exc) {
        if (exc.message && exc.message !== 'daemon_off') {
          window.alert(`❌ ${exc.message}`);
          if (btn) {
            btn.disabled = false;
            btn.textContent = label;
          }
          return;
        }
      }
    }

    window.alert('Час очікування вичерпано. Завершіть вхід у Google або спробуйте ще раз.');
    if (btn) {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  function renderBody(focusKey) {
    if (focusKey) activeKey = focusKey;
    body.innerHTML = integrations.map((item) => {
      const st = item.status || 'off';
      const fieldsHtml = (item.fields || []).map((field) => {
        if (field.type === 'service_account') {
          const fileHint = field.file_exists ? 'файл існує' : 'файл відсутній';
          return `
            <div class="mkt-int-field" data-field-env="${esc(field.env)}">
              <label class="mkt-int-field__label">${esc(field.label)} <span style="color:#94a3b8">(${fileHint})</span></label>
              <div class="mkt-int-field__preview">${esc(field.path || field.preview || '—')}</div>
              ${isAdmin ? `<textarea class="mkt-int-field__input mkt-int-field__input--textarea" name="${esc(field.env)}" placeholder="${esc(field.placeholder || '')}" ${item.editable === false ? 'disabled' : ''}></textarea>` : ''}
            </div>`;
        }
        return `
          <div class="mkt-int-field" data-field-env="${esc(field.env)}">
            <label class="mkt-int-field__label">${esc(field.label)} <span style="color:#94a3b8">(${esc(field.env)})</span></label>
            <div class="mkt-int-field__preview">${esc(field.preview || '—')}</div>
            ${isAdmin ? `<input class="mkt-int-field__input" type="${field.secret ? 'password' : 'text'}" name="${esc(field.env)}" placeholder="${esc(field.placeholder || '')}" autocomplete="off" ${item.editable === false ? 'disabled' : ''}>` : ''}
          </div>`;
      }).join('');

      const note = item.note ? `<p class="mkt-int-block__note">${esc(item.note)}</p>` : '';
      let authHelp = '';
      if (item.auth_help) {
        const h = item.auth_help;
        const steps = (h.steps || []).map((s, i) => `<li>${esc(s)}</li>`).join('');
        const links = (h.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join(' · ');
        authHelp = `
          <div class="mkt-int-block__help">
            <p class="mkt-int-block__help-summary">${esc(h.summary || '')}</p>
            <ol class="mkt-int-block__help-steps">${steps}</ol>
            ${links ? `<p class="mkt-int-block__help-links">${links}</p>` : ''}
          </div>`;
      }
      let oauthHtml = '';
      if (item.key === 'google_ads' && item.oauth) {
        const o = item.oauth;
        const returnTo = encodeURIComponent(window.location.pathname);
        if (isAdmin && o.available) {
          const credsUrl = esc(o.google_credentials_url || 'https://console.cloud.google.com/apis/credentials');
          const local = o.local || {};
          const localDaemonCmd = esc(local.daemon_command || 'python scripts/google_ads_oauth_cli.py --daemon');
          const localRedirect = local.redirect_uri ? esc(local.redirect_uri) : 'http://localhost:8765';
          const localBase = esc(local.daemon_base_url || 'http://localhost:8765');
          const localBlock = local.available
            ? `<div class="mkt-int-oauth mkt-int-oauth--local" data-local-oauth-base="${localBase}">
                <p class="mkt-int-oauth__title">Підключення Google Ads</p>
                <p class="mkt-int-block__note">1. У терміналі на цьому компʼютері: <code class="mkt-int-oauth__cmd">${localDaemonCmd}</code>
                  <button type="button" class="mkt-int-oauth__copy" data-copy-oauth-uri="${localDaemonCmd}">Копіювати</button>
                </p>
                <p class="mkt-int-block__note">2. Натисніть кнопку — відкриється вікно входу Google (через scripts/google_ads_oauth_cli.py).</p>
                <button type="button" class="mkt-int-oauth__btn" data-google-local-connect data-label="Підключити через Google">Підключити через Google</button>
                <p class="mkt-int-oauth__status" data-google-local-status hidden></p>
                <p class="mkt-int-oauth__hint">Redirect URI: <code>${localRedirect}</code> (має бути в Google Cloud)</p>
              </div>`
            : '';
          const clientHint = o.client_id_hint
            ? `<p class="mkt-int-oauth__hint">OAuth client ID: <code>${esc(o.client_id_hint)}</code></p>`
            : '';
          const jsOrigin = o.javascript_origin
            ? `<p class="mkt-int-oauth__hint">Authorized JavaScript origin: <code>${esc(o.javascript_origin)}</code></p>`
            : '';
          const redirectHint = o.redirect_uri
            ? `<details class="mkt-int-oauth__web">
                <summary>Альтернатива: підключення через веб-сервер</summary>
                <div class="mkt-int-oauth__uri">
                  <span>Додайте <strong>Authorized redirect URI</strong> у <a href="${credsUrl}" target="_blank" rel="noopener">Google Cloud</a>:</span>
                  <code>${esc(o.redirect_uri)}</code>
                  <button type="button" class="mkt-int-oauth__copy" data-copy-oauth-uri="${esc(o.redirect_uri)}">Копіювати URI</button>
                  ${jsOrigin}
                  ${clientHint}
                </div>
                <a class="mkt-int-oauth__btn mkt-int-oauth__btn--secondary" href="${esc(o.connect_url)}?return_to=${returnTo}">Підключити через Google (веб)</a>
              </details>`
            : '';
          oauthHtml = `${localBlock}${redirectHint}`;
        } else if (!o.available) {
          oauthHtml = '<p class="mkt-int-block__note">Для OAuth спочатку збережіть Client ID і Client Secret у полях нижче.</p>';
        }
      }

      const content = oauthHtml + (note || '') + (fieldsHtml || '') || '<p class="mkt-int-block__note">Немає ключів для редагування.</p>';
      const err = item.error ? `<p class="mkt-int-block__error">${esc(item.error)}</p>` : '';
      const hidden = integrations.length > 1 && activeKey && item.key !== activeKey ? ' hidden' : '';
      return `
        <section class="mkt-int-block" data-integration="${esc(item.key)}"${hidden}>
          <div class="mkt-int-block__head">
            <h3 class="mkt-int-block__title">${esc(item.label)}</h3>
            <span class="mkt-int-status mkt-int-status--${esc(st)}">${esc(statusLabels[st] || st)}</span>
          </div>
          ${authHelp}
          ${content}
          ${err}
        </section>`;
    }).join('');

    if (integrations.length > 1) {
      const tabs = integrations.map((item) => {
        const active = item.key === activeKey ? ' is-active' : '';
        return `<button type="button" class="mkt-period-preset mkt-int-tab${active}" data-int-tab="${esc(item.key)}">${esc(item.label)}</button>`;
      }).join('');
      body.insertAdjacentHTML('afterbegin', `<div class="mkt-period-presets mkt-int-tabs">${tabs}</div>`);
      body.querySelectorAll('[data-int-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
          activeKey = btn.getAttribute('data-int-tab');
          renderBody(activeKey);
        }, { signal });
      });
    }

    body.querySelectorAll('[data-copy-oauth-uri]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const text = btn.getAttribute('data-copy-oauth-uri') || '';
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          const prev = btn.textContent;
          btn.textContent = 'Скопійовано';
          setTimeout(() => { btn.textContent = prev; }, 2000);
        } catch (_) {
          window.prompt('Скопіюйте:', text);
        }
      }, { signal });
    });

    const googleItem = integrations.find((item) => item.key === 'google_ads');
    const googleLocal = googleItem && googleItem.oauth ? googleItem.oauth.local : null;
    body.querySelectorAll('[data-google-local-connect]').forEach((btn) => {
      btn.addEventListener('click', () => startLocalGoogleOAuth(googleLocal, btn), { signal });
    });
  }

  function openDialog(focusKey) {
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = '';
    }
    renderBody(focusKey || activeKey);
    dialog.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function closeDialog() {
    dialog.hidden = true;
    document.body.style.overflow = '';
  }

  if (openBtn) openBtn.addEventListener('click', () => openDialog(), { signal });
  document.querySelectorAll('.mkt-open-integration-settings').forEach((btn) => {
    btn.addEventListener('click', () => {
      openDialog(btn.getAttribute('data-integration-key'));
    }, { signal });
  });
  dialog.querySelectorAll('[data-mkt-int-close]').forEach((el) => {
    el.addEventListener('click', closeDialog, { signal });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !dialog.hidden) closeDialog();
  }, { signal });

  if (saveBtn) saveBtn.addEventListener('click', async () => {
    if (!isAdmin || !activeKey) return;
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = '';
    }
    const block = body.querySelector(`[data-integration="${activeKey}"]`);
    if (!block) return;
    const fd = new FormData();
    fd.append('csrf_token', csrf);
    block.querySelectorAll('input[name], textarea[name]').forEach((el) => {
      const val = (el.value || '').trim();
      if (val) fd.append(el.name, val);
    });
    saveBtn.disabled = true;
    try {
      const res = await fetch(`/mkt-report/integrations/${encodeURIComponent(activeKey)}`, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Не вдалося зберегти ключі');
      }
      closeDialog();
      const url = new URL(window.location.href);
      url.searchParams.set('integration_saved', '1');
      if (data.verified) {
        url.searchParams.set('integration_verified', data.verified);
      }
      window.location.href = url.toString();
    } catch (err) {
      if (errEl) {
        errEl.hidden = false;
        errEl.textContent = err.message || String(err);
      }
    } finally {
      saveBtn.disabled = !isAdmin;
    }
  }, { signal });
}
