(function () {
    var el = document.getElementById('clients-dash-json');
    if (!el) return;
    var DASH = JSON.parse(el.textContent || '{}');
    var Chart = window.Chart;
    if (!Chart) return;

    var font = { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", size: 12 };
    var gridSoft = { color: 'rgba(148, 163, 184, 0.22)' };

    function fmtUaNum(n) {
        var x = Math.round(Number(n) || 0);
        return String(x).replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
    }

    var monthly = DASH.monthly || [];
    var chartGranularity = (DASH.monthly_chart_meta && DASH.monthly_chart_meta.granularity) || 'month';
    var monthlyOutcome = (DASH.monthly_chart_filter && DASH.monthly_chart_filter.outcome) || 'all';
    var monthlySplitBars = monthlyOutcome === 'all';
    /** Заокруглення двошарової «свічки»: червоний низ (неуспіш.+проміж.), зелений верх (успіш.). */
    function monthlyStackRadius(ctx) {
        var row = monthly[ctx.dataIndex];
        if (!row) return 0;
        var nu = Number(row.orders_unsuccessful) || 0;
        var nt = Number(row.orders_transitional) || 0;
        var ns = Number(row.orders_success) || 0;
        var bottom = nu + nt;
        var di = ctx.datasetIndex;
        var R = 6;
        var cap = { topLeft: R, topRight: R, bottomLeft: R, bottomRight: R };
        var topOnly = { topLeft: R, topRight: R, bottomLeft: 0, bottomRight: 0 };
        var botOnly = { topLeft: 0, topRight: 0, bottomLeft: R, bottomRight: R };
        if (di === 0) {
            if (!bottom) return 0;
            if (!ns) return cap;
            return botOnly;
        }
        if (di === 1) {
            if (!ns) return 0;
            if (!bottom) return cap;
            return topOnly;
        }
        return 0;
    }
    if (monthly.length && document.getElementById('chartMonthly')) {
        var monthlyLabels = monthly.map(function (x) { return x.label_ua || x.ym; });
        var barDatasets;
        if (monthlySplitBars) {
            /* Одна «свічка»: низ — червоний (неуспіш. + проміж.), верх — зелений (успіш.) */
            barDatasets = [
                {
                    label: 'Неуспішні та проміжні',
                    data: monthly.map(function (x) {
                        return (Number(x.orders_unsuccessful) || 0) + (Number(x.orders_transitional) || 0);
                    }),
                    yAxisID: 'y',
                    stack: 'orders',
                    order: 2,
                    backgroundColor: 'rgba(239, 68, 68, 0.92)',
                    borderRadius: monthlyStackRadius,
                    borderSkipped: false,
                    maxBarThickness: 34,
                },
                {
                    label: 'Успішні',
                    data: monthly.map(function (x) { return Number(x.orders_success) || 0; }),
                    yAxisID: 'y',
                    stack: 'orders',
                    order: 2,
                    backgroundColor: 'rgba(34, 197, 94, 0.9)',
                    borderRadius: monthlyStackRadius,
                    borderSkipped: false,
                    maxBarThickness: 34,
                },
            ];
        } else {
            var monoBarLabel = monthlyOutcome === 'success' ? 'Успішні' : 'Неуспішні';
            var monoBarColor = monthlyOutcome === 'success'
                ? 'rgba(34, 197, 94, 0.88)'
                : 'rgba(239, 68, 68, 0.9)';
            barDatasets = [
                {
                    label: monoBarLabel,
                    data: monthly.map(function (x) { return x.orders; }),
                    yAxisID: 'y',
                    order: 2,
                    backgroundColor: monoBarColor,
                    borderRadius: { topLeft: 6, topRight: 6, bottomLeft: 0, bottomRight: 0 },
                    borderSkipped: false,
                    maxBarThickness: 36,
                },
            ];
        }
        var revDataset = {
            label: 'Виручка (успішні), ₴',
            data: monthly.map(function (x) { return x.revenue; }),
            yAxisID: 'y1',
            type: 'line',
            order: 1,
            borderColor: 'rgba(5, 150, 105, 1)',
            backgroundColor: 'rgba(16, 185, 129, 0.08)',
            fill: true,
            tension: 0.35,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: '#fff',
            pointBorderColor: 'rgba(5, 150, 105, 1)',
            pointBorderWidth: 2,
            borderWidth: 2.5,
        };
        var revDsIndex = barDatasets.length;
        var legendSlot = document.getElementById('monthly-chart-series-toggles');
        function attachMonthlySeriesToggles(chart, slot, revIndex) {
            if (!slot) return;
            slot.innerHTML = '';
            var n = chart.data.datasets.length;
            var order = [];
            for (var j = 0; j < n; j++) order.push(j);
            order.sort(function (a, b) {
                var aRev = a === revIndex ? 0 : 1;
                var bRev = b === revIndex ? 0 : 1;
                if (aRev !== bRev) return aRev - bRev;
                return a - b;
            });
            order.forEach(function (i) {
                var ds = chart.data.datasets[i];
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'monthly-chart-series-btn is-on';
                btn.setAttribute('aria-pressed', 'true');
                var sw = document.createElement('span');
                sw.className = 'monthly-chart-series-swatch';
                sw.setAttribute('aria-hidden', 'true');
                if (ds.type === 'line') {
                    sw.style.cssText =
                        'display:inline-block;width:20px;height:0;vertical-align:middle;border-radius:0;border-bottom:3px solid ' +
                        String(ds.borderColor || '#059669');
                } else {
                    sw.style.cssText =
                        'display:inline-block;width:10px;height:10px;border-radius:3px;vertical-align:middle;background:' +
                        (typeof ds.backgroundColor === 'string' ? ds.backgroundColor : '#94a3b8');
                }
                var lab = document.createElement('span');
                lab.className = 'monthly-chart-series-btn-label';
                lab.textContent = ds.label || '';
                btn.appendChild(sw);
                btn.appendChild(lab);
                btn.addEventListener('click', function () {
                    var next = !chart.isDatasetVisible(i);
                    chart.setDatasetVisibility(i, next);
                    chart.update();
                    btn.classList.toggle('is-on', next);
                    btn.setAttribute('aria-pressed', next ? 'true' : 'false');
                });
                slot.appendChild(btn);
            });
        }
        var monthlyChart = new Chart(document.getElementById('chartMonthly'), {
            type: 'bar',
            data: {
                labels: monthlyLabels,
                datasets: barDatasets.concat([revDataset]),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { top: 2, bottom: 2 } },
                interaction: { mode: 'index', intersect: false },
                datasets: {
                    bar: {
                        categoryPercentage: 0.82,
                        barPercentage: monthlySplitBars ? 0.88 : 0.92,
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var row = monthly[items[0].dataIndex];
                                if (!row) return items[0].label || '';
                                var line1 = (row.ym || '') + ' · ' + (row.label_ua || row.ym || '');
                                if (row.orders != null)
                                    return [line1, 'Усього замовлень: ' + fmtUaNum(row.orders)];
                                return line1;
                            },
                            label: function (ctx) {
                                var v = ctx.parsed.y;
                                var di = ctx.datasetIndex;
                                if (di === revDsIndex) return ' Виручка (успішні): ' + fmtUaNum(v) + ' ₴';
                                if (monthlySplitBars) {
                                    if (di === 0) {
                                        var row0 = monthly[ctx.dataIndex];
                                        var nu0 = row0 ? (Number(row0.orders_unsuccessful) || 0) : 0;
                                        var nt0 = row0 ? (Number(row0.orders_transitional) || 0) : 0;
                                        var parts = [];
                                        if (nu0) parts.push('неуспіш.: ' + fmtUaNum(nu0));
                                        if (nt0) parts.push('проміж.: ' + fmtUaNum(nt0));
                                        var tail = parts.length ? ' (' + parts.join(', ') + ')' : '';
                                        return ' Неуспіш. та проміж.: ' + fmtUaNum(v) + tail;
                                    }
                                    if (di === 1) return ' Успішні: ' + fmtUaNum(v);
                                }
                                return ' Замовлень: ' + fmtUaNum(v);
                            },
                            afterBody: function (items) {
                                if (!items.length) return [];
                                var row = monthly[items[0].dataIndex];
                                if (!row) return [];
                                var di = items[0].datasetIndex;
                                var lines = [];
                                if (monthlySplitBars && di !== revDsIndex && row.revenue_success != null) {
                                    lines.push('Виручка (успіш.): ' + fmtUaNum(Math.round(row.revenue_success)) + ' ₴');
                                    lines.push('Виручка (неуспіш.): ' + fmtUaNum(Math.round(row.revenue_unsuccessful)) + ' ₴');
                                    var rt = Number(row.revenue_transitional);
                                    if (rt) lines.push('Виручка (проміж.): ' + fmtUaNum(Math.round(rt)) + ' ₴');
                                }
                                if (row.distinct_buyers != null)
                                    lines.push('Унік. покупців: ' + fmtUaNum(row.distinct_buyers));
                                if (row.avg_check_uah != null)
                                    lines.push('Сер. чек: ' + fmtUaNum(Math.round(row.avg_check_uah)) + ' ₴');
                                return lines;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        type: 'category',
                        offset: true,
                        stacked: monthlySplitBars,
                        grid: { display: false, drawBorder: false },
                        ticks: {
                            font: font,
                            autoSkip: chartGranularity === 'day',
                            maxTicksLimit: chartGranularity === 'day' ? 18 : 24,
                            maxRotation: chartGranularity === 'day' ? 45 : 58,
                            minRotation: chartGranularity === 'day' ? 0 : 22,
                            padding: 2,
                        },
                    },
                    y: {
                        type: 'linear',
                        position: 'left',
                        stacked: monthlySplitBars,
                        title: { display: true, text: 'Кількість замовлень', font: { ...font, weight: '600' }, color: '#475569' },
                        grid: { ...gridSoft, drawBorder: false },
                        ticks: { font: font, color: '#64748b' },
                        beginAtZero: true,
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        title: { display: true, text: 'Виручка, ₴', font: { ...font, weight: '600' }, color: '#047857' },
                        grid: { drawOnChartArea: false, drawBorder: false },
                        ticks: {
                            font: font,
                            color: '#059669',
                            callback: function (val) { return fmtUaNum(val); },
                        },
                        beginAtZero: true,
                    },
                },
            },
        });
        attachMonthlySeriesToggles(monthlyChart, legendSlot, revDsIndex);
    }

    var mForm = document.getElementById('clients-monthly-form');
    var ySel = document.getElementById('dash-monthly-year-preset');
    if (mForm && ySel) {
        ySel.addEventListener('change', function () {
            var y = this.value;
            if (!y) return;
            var fi = mForm.querySelector('[name="monthly_from"]');
            var ti = mForm.querySelector('[name="monthly_to"]');
            if (fi) fi.value = y + '-01-01';
            if (ti) ti.value = y + '-12-31';
            mForm.submit();
        });
    }
})();
