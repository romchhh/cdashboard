/* ═══════════════════════════════════════════════════
   advertising.js — Chart.js для дашборду реклами
═══════════════════════════════════════════════════ */

const AD_CHARTS = [];
let periodPickerAbort = null;

document.addEventListener('DOMContentLoaded', () => {
  try {
    if (typeof Chart !== 'undefined') {
      Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
      Chart.defaults.color = '#64748b';
      Chart.defaults.borderColor = '#e2e8f0';
      Chart.defaults.plugins.legend.labels.boxWidth = 10;
      Chart.defaults.plugins.legend.labels.boxHeight = 10;
    }
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

function initPeriodPicker() {
  if (typeof AbortController === 'undefined') return;
  if (periodPickerAbort) periodPickerAbort.abort();
  periodPickerAbort = new AbortController();
  const { signal } = periodPickerAbort;

  const pill = document.getElementById('adPeriodPill');
  const pop = document.getElementById('adPeriodPopover');
  const form = pill?.closest('form');
  if (!pill || !pop || !form) return;

  const fromHidden = form.querySelector('input[name="date_from"]');
  const toHidden = form.querySelector('input[name="date_to"]');
  const fromPop = pop.querySelector('[data-period-from]');
  const toPop = pop.querySelector('[data-period-to]');
  const maxDate = fromPop?.max || toPop?.max || '';

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

  function applyRange(from, to) {
    const fmt = d => d.toISOString().slice(0, 10);
    if (fromPop) fromPop.value = fmt(from);
    if (toPop) toPop.value = fmt(to);
  }

  pop.querySelectorAll('.ad-period-preset').forEach(btn => {
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
      pop.querySelectorAll('.ad-period-preset').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
    }, { signal });
  });

  form.addEventListener('submit', () => {
    if (fromPop && fromHidden) fromHidden.value = fromPop.value;
    if (toPop && toHidden) toHidden.value = toPop.value;
  }, { signal });
}

function initMiniCharts() {
  document.querySelectorAll('canvas.ad-mini-chart[data-chart]').forEach(canvas => {
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
  const canvas = document.getElementById('adMainChart');
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
                return `${ctx.label}: ${fmtNum(ctx.parsed)} ₴ (${pct}%)`;
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
  document.querySelectorAll('table[data-sortable]').forEach(table => {
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
  document.querySelectorAll('.ad-card').forEach(card => {
    const table = card.querySelector('table[data-sortable]');
    if (!table) return;

    let input = card.querySelector('.ad-table-search');
    const head = card.querySelector('.ad-card__head');
    const countEl = card.querySelector('[data-table-count]');

    if (!input && head) {
      head.classList.add('ad-card__head--table');
      const headLeft = head.querySelector('.ad-card__head-left');
      if (!headLeft && head.querySelector('.ad-card__title')) {
        const title = head.querySelector('.ad-card__title');
        const wrap = document.createElement('div');
        wrap.className = 'ad-card__head-left';
        const cnt = document.createElement('span');
        cnt.className = 'ad-table-count';
        cnt.dataset.tableCount = '';
        wrap.appendChild(title);
        wrap.appendChild(cnt);
        head.insertBefore(wrap, head.firstChild);
      }
      input = document.createElement('input');
      input.type = 'search';
      input.className = 'ad-table-search';
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
  const form = document.querySelector('[data-ad-period-form]');
  const compareInput = form?.querySelector('[data-compare-input]');
  if (!form || !compareInput) return;

  form.querySelectorAll('.ad-period-compare-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      compareInput.value = btn.dataset.compare || 'prev';
      form.querySelectorAll('.ad-period-compare-btn').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
    });
  });
}

function destroyAdCharts() {
  while (AD_CHARTS.length) {
    const ch = AD_CHARTS.pop();
    try { ch.destroy(); } catch (_) { /* ignore */ }
  }
}

function reinitAdPageScripts() {
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

let metricHelpBound = false;
let metricHelpFloat = null;
let metricHelpAnchor = null;

function ensureMetricHelpFloat() {
  if (metricHelpFloat && document.body.contains(metricHelpFloat)) return metricHelpFloat;
  const el = document.createElement('div');
  el.id = 'adMetricHelpFloat';
  el.className = 'ad-metric-help-float';
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
    document.querySelectorAll('.ad-metric-help.is-open').forEach((node) => node.classList.remove('is-open'));
    metricHelpAnchor = null;
  }
}

function placeMetricHelpFloat(help) {
  const source = help.querySelector('.ad-metric-help__tip');
  const text = (source && source.textContent || '').trim();
  if (!text) return;
  const el = ensureMetricHelpFloat();
  el.textContent = text;
  el.hidden = false;
  el.style.left = '0px';
  el.style.top = '0px';
  const icon = help.querySelector('.ad-metric-help__icon') || help;
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
    const help = e.target.closest('.ad-metric-help');
    if (!help) return;
    const from = e.relatedTarget;
    if (from && help.contains(from)) return;
    placeMetricHelpFloat(help);
  });

  document.addEventListener('pointerout', (e) => {
    const help = e.target.closest('.ad-metric-help');
    if (!help) return;
    const to = e.relatedTarget;
    if (to && help.contains(to)) return;
    if (help.classList.contains('is-open')) return;
    if (metricHelpAnchor === help) hideMetricHelpFloat();
  });

  document.addEventListener('focusin', (e) => {
    const help = e.target.closest('.ad-metric-help');
    if (help) placeMetricHelpFloat(help);
  });

  document.addEventListener('focusout', (e) => {
    const help = e.target.closest('.ad-metric-help');
    if (!help || help.classList.contains('is-open')) return;
    if (metricHelpAnchor === help) hideMetricHelpFloat();
  });

  document.addEventListener('click', (e) => {
    const help = e.target.closest('.ad-metric-help');
    if (help) {
      e.stopPropagation();
      const wasOpen = help.classList.contains('is-open');
      document.querySelectorAll('.ad-metric-help.is-open').forEach((el) => el.classList.remove('is-open'));
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
  const select = document.querySelector('[data-chart-granularity]');
  const form = document.querySelector('[data-ad-period-form]');
  if (!select || !form) return;

  select.addEventListener('change', () => {
    const input = form.querySelector('[data-chart-granularity-input]');
    if (input) input.value = select.value;
    const params = new URLSearchParams(window.location.search);
    params.set('chart_granularity', select.value);
    const href = `${window.location.pathname}?${params.toString()}`;
    if (typeof loadAdPartial === 'function' && document.querySelector('.ad-wrap')) {
      loadAdPartial(href, { push: true });
      return;
    }
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.submit();
  });
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
  document.querySelectorAll('.ad-section-nav .ad-nav-link').forEach((a) => {
    const linkPath = (a.getAttribute('href') || '').split('?')[0];
    const active = linkPath === pathname;
    a.classList.toggle('is-active', active);
    if (active) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

let adPartialAbort = null;

function updateAdChromeFromDoc(doc) {
  const newWrap = doc.querySelector('.ad-wrap');
  if (!newWrap) return;

  const wrap = document.querySelector('.ad-wrap');
  if (wrap && newWrap.dataset.section) wrap.dataset.section = newWrap.dataset.section;

  const newTitle = newWrap.querySelector('.ad-topbar__title');
  const title = document.querySelector('.ad-topbar__title');
  if (newTitle && title) title.innerHTML = newTitle.innerHTML;

  const newForm = newWrap.querySelector('[data-ad-period-form]');
  const form = document.querySelector('[data-ad-period-form]');
  if (newForm && form) form.action = newForm.getAttribute('action') || form.action;

  const newToolbar = newWrap.querySelector('.ad-toolbar__actions');
  const toolbar = document.querySelector('.ad-toolbar__actions');
  if (newToolbar && toolbar) {
    toolbar.querySelectorAll('a.ad-icon-btn').forEach((link, i) => {
      const next = newToolbar.querySelectorAll('a.ad-icon-btn')[i];
      if (next) link.href = next.href;
    });
  }
}

async function loadAdPartial(href, { push = true, replace = false } = {}) {
  const wrap = document.querySelector('.ad-wrap');
  const pageBody = document.getElementById('adPageBody');
  if (!wrap || typeof AbortController === 'undefined' || typeof fetch === 'undefined') {
    window.location.href = href;
    return;
  }

  const target = adMergePeriodParams(href);
  const fetchUrl = target.pathname + target.search + (target.search ? '&' : '?') + 'partial=1';

  setAdNavActive(target.pathname);

  if (adPartialAbort) adPartialAbort.abort();
  adPartialAbort = new AbortController();

  if (typeof hideMetricHelpFloat === 'function') hideMetricHelpFloat();

  if (pageBody) pageBody.classList.add('ad-page-body--loading');
  const loadingTimer = setTimeout(() => {
    const el = document.getElementById('adPageBody');
    if (el) el.classList.remove('ad-page-body--loading');
  }, 20000);
  try {
    const res = await fetch(fetchUrl, {
      headers: { 'X-Requested-With': 'ad-spa' },
      signal: adPartialAbort.signal,
    });
    if (!res.ok) throw new Error('load failed');
    const html = await res.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const newWrap = doc.querySelector('.ad-wrap');
    if (!newWrap) throw new Error('no content');

    const newBody = newWrap.querySelector('#adPageBody');
    if (pageBody && newBody) {
      pageBody.innerHTML = newBody.innerHTML;
      updateAdChromeFromDoc(doc);
    } else {
      wrap.replaceWith(newWrap);
      setAdNavActive(target.pathname);
    }

    const displayUrl = target.pathname + target.search;
    if (push) history.pushState({ adSpa: true }, '', displayUrl);
    else if (replace) history.replaceState({ adSpa: true }, '', displayUrl);

    reinitAdPageScripts();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    if (pageBody) {
      pageBody.innerHTML = (
        '<div class="ad-section"><div class="ad-alert ad-alert--auth">'
        + '⚠️ Не вдалося завантажити розділ. '
        + '<button type="button" class="ad-alert__btn" onclick="window.location.reload()">Оновити</button>'
        + ' або <a href="' + (target.pathname + target.search) + '">відкрити повністю</a>.'
        + '</div></div>'
      );
      return;
    }
    window.location.href = target.pathname + target.search;
  } finally {
    clearTimeout(loadingTimer);
    const el = document.getElementById('adPageBody');
    if (el) el.classList.remove('ad-page-body--loading');
  }
}

function initAdSpaNav() {
  if (!document.querySelector('.ad-wrap')) return;
  if (window.__adSpaNavBound) return;
  window.__adSpaNavBound = true;

  document.addEventListener('click', async (e) => {
    const link = e.target.closest('a.ad-nav-link, a.ad-channel-link');
    if (!link || link.target === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const href = link.getAttribute('href');
    if (!href || !(href.startsWith('/clients/ad-analytics') || href.startsWith('/clients/advertising'))) return;
    if (href.includes('/export.') || href.includes('sync_crm=1')) return;

    e.preventDefault();
    await loadAdPartial(href, { push: true });
  });

  window.addEventListener('popstate', () => {
    if (!window.location.pathname.startsWith('/clients/ad-analytics') && !window.location.pathname.startsWith('/clients/advertising')) return;
    loadAdPartial(window.location.pathname + window.location.search, { push: false, replace: true });
  });
}

function fmtNum(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1) + ' млн';
  if (abs >= 10_000) return Math.round(n).toLocaleString('uk-UA');
  if (abs >= 1_000) return n.toLocaleString('uk-UA');
  return Number.isInteger(n) ? String(n) : Number(n).toFixed(1);
}

function fmtMoneyAxis(n) {
  if (n === null || n === undefined || isNaN(n)) return '';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (abs >= 1_000) return (n / 1_000).toFixed(0) + 'k';
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

  const wrap = document.querySelector('.ad-wrap[data-integrations]');
  const dialog = document.getElementById('adIntegrationDialog');
  const body = document.getElementById('adIntegrationDialogBody');
  const errEl = document.getElementById('adIntegrationDialogError');
  const saveBtn = document.getElementById('adIntegrationSaveBtn');
  const openBtn = document.getElementById('adIntegrationSettingsBtn');
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
  let activeKey = integrations[0]?.key || null;

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
            <div class="ad-int-field" data-field-env="${esc(field.env)}">
              <label class="ad-int-field__label">${esc(field.label)} <span style="color:#94a3b8">(${fileHint})</span></label>
              <div class="ad-int-field__preview">${esc(field.path || field.preview || '—')}</div>
              ${isAdmin ? `<textarea class="ad-int-field__input ad-int-field__input--textarea" name="${esc(field.env)}" placeholder="${esc(field.placeholder || '')}" ${item.editable === false ? 'disabled' : ''}></textarea>` : ''}
            </div>`;
        }
        return `
          <div class="ad-int-field" data-field-env="${esc(field.env)}">
            <label class="ad-int-field__label">${esc(field.label)} <span style="color:#94a3b8">(${esc(field.env)})</span></label>
            <div class="ad-int-field__preview">${esc(field.preview || '—')}</div>
            ${isAdmin ? `<input class="ad-int-field__input" type="${field.secret ? 'password' : 'text'}" name="${esc(field.env)}" placeholder="${esc(field.placeholder || '')}" autocomplete="off" ${item.editable === false ? 'disabled' : ''}>` : ''}
          </div>`;
      }).join('');

      const note = item.note ? `<p class="ad-int-block__note">${esc(item.note)}</p>` : '';
      let authHelp = '';
      if (item.auth_help) {
        const h = item.auth_help;
        const steps = (h.steps || []).map((s, i) => `<li>${esc(s)}</li>`).join('');
        const links = (h.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join(' · ');
        authHelp = `
          <div class="ad-int-block__help">
            <p class="ad-int-block__help-summary">${esc(h.summary || '')}</p>
            <ol class="ad-int-block__help-steps">${steps}</ol>
            ${links ? `<p class="ad-int-block__help-links">${links}</p>` : ''}
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
            ? `<div class="ad-int-oauth ad-int-oauth--local" data-local-oauth-base="${localBase}">
                <p class="ad-int-oauth__title">Підключення Google Ads</p>
                <p class="ad-int-block__note">1. У терміналі на цьому компʼютері: <code class="ad-int-oauth__cmd">${localDaemonCmd}</code>
                  <button type="button" class="ad-int-oauth__copy" data-copy-oauth-uri="${localDaemonCmd}">Копіювати</button>
                </p>
                <p class="ad-int-block__note">2. Натисніть кнопку — відкриється вікно входу Google (через scripts/google_ads_oauth_cli.py).</p>
                <button type="button" class="ad-int-oauth__btn" data-google-local-connect data-label="Підключити через Google">Підключити через Google</button>
                <p class="ad-int-oauth__status" data-google-local-status hidden></p>
                <p class="ad-int-oauth__hint">Redirect URI: <code>${localRedirect}</code> (має бути в Google Cloud)</p>
              </div>`
            : '';
          const clientHint = o.client_id_hint
            ? `<p class="ad-int-oauth__hint">OAuth client ID: <code>${esc(o.client_id_hint)}</code></p>`
            : '';
          const jsOrigin = o.javascript_origin
            ? `<p class="ad-int-oauth__hint">Authorized JavaScript origin: <code>${esc(o.javascript_origin)}</code></p>`
            : '';
          const redirectHint = o.redirect_uri
            ? `<details class="ad-int-oauth__web">
                <summary>Альтернатива: підключення через веб-сервер</summary>
                <div class="ad-int-oauth__uri">
                  <span>Додайте <strong>Authorized redirect URI</strong> у <a href="${credsUrl}" target="_blank" rel="noopener">Google Cloud</a>:</span>
                  <code>${esc(o.redirect_uri)}</code>
                  <button type="button" class="ad-int-oauth__copy" data-copy-oauth-uri="${esc(o.redirect_uri)}">Копіювати URI</button>
                  ${jsOrigin}
                  ${clientHint}
                </div>
                <a class="ad-int-oauth__btn ad-int-oauth__btn--secondary" href="${esc(o.connect_url)}?return_to=${returnTo}">Підключити через Google (веб)</a>
              </details>`
            : '';
          oauthHtml = `${localBlock}${redirectHint}`;
        } else if (!o.available) {
          oauthHtml = '<p class="ad-int-block__note">Для OAuth спочатку збережіть Client ID і Client Secret у полях нижче.</p>';
        }
      }

      const content = oauthHtml + (note || '') + (fieldsHtml || '') || '<p class="ad-int-block__note">Немає ключів для редагування.</p>';
      const err = item.error ? `<p class="ad-int-block__error">${esc(item.error)}</p>` : '';
      const hidden = integrations.length > 1 && activeKey && item.key !== activeKey ? ' hidden' : '';
      return `
        <section class="ad-int-block" data-integration="${esc(item.key)}"${hidden}>
          <div class="ad-int-block__head">
            <h3 class="ad-int-block__title">${esc(item.label)}</h3>
            <span class="ad-int-status ad-int-status--${esc(st)}">${esc(statusLabels[st] || st)}</span>
          </div>
          ${authHelp}
          ${content}
          ${err}
        </section>`;
    }).join('');

    if (integrations.length > 1) {
      const tabs = integrations.map((item) => {
        const active = item.key === activeKey ? ' is-active' : '';
        return `<button type="button" class="ad-period-preset ad-int-tab${active}" data-int-tab="${esc(item.key)}">${esc(item.label)}</button>`;
      }).join('');
      body.insertAdjacentHTML('afterbegin', `<div class="ad-period-presets ad-int-tabs">${tabs}</div>`);
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

  openBtn?.addEventListener('click', () => openDialog(), { signal });
  document.querySelectorAll('.ad-open-integration-settings').forEach((btn) => {
    btn.addEventListener('click', () => {
      openDialog(btn.getAttribute('data-integration-key'));
    }, { signal });
  });
  dialog.querySelectorAll('[data-ad-int-close]').forEach((el) => {
    el.addEventListener('click', closeDialog, { signal });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !dialog.hidden) closeDialog();
  }, { signal });

  saveBtn?.addEventListener('click', async () => {
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
      const res = await fetch(`/ad-analytics/integrations/${encodeURIComponent(activeKey)}`, {
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
