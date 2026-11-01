/**
 * Клієнтське сортування таблиць: перший клік по заголовку — зростання (asc),
 * повторний по тій самій колонці — спадання (desc).
 * Таблиця: class="js-sortable". Колонка: data-sort-type="num|text|firststrong", data-sort-skip — пропустити.
 * Опційно на <td>: data-sort-value="123" або data-sort-value="текст" для точного порівняння.
 */
(function () {
  'use strict';

  function parseFirstNumber(text) {
    if (text == null || text === '') return NaN;
    var t = String(text).replace(/\u00a0/g, ' ').replace(/−/g, '-');
    var m = t.match(/-?\d[\d\s]*[.,]?\d*/);
    if (!m) return NaN;
    var n = m[0].replace(/\s/g, '').replace(',', '.');
    var v = parseFloat(n);
    return Number.isFinite(v) ? v : NaN;
  }

  function cellSortPayload(td, type) {
    if (!td) {
      return { kind: 'empty', v: '' };
    }
    var raw = td.getAttribute('data-sort-value');
    if (raw !== null && raw !== '') {
      var nf = parseFloat(raw);
      if (!Number.isNaN(nf) && String(raw).trim().match(/^-?\d/)) {
        return { kind: 'num', v: nf };
      }
      return { kind: 'str', v: String(raw).trim().toLowerCase() };
    }

    if (type === 'firststrong') {
      var el = td.querySelector('strong');
      var t = el ? el.textContent : '';
      var fv = parseFirstNumber(t);
      return { kind: 'num', v: Number.isFinite(fv) ? fv : NaN };
    }
    if (type === 'num') {
      var nv = parseFirstNumber(td.innerText);
      return { kind: 'num', v: Number.isFinite(nv) ? nv : NaN };
    }
    return { kind: 'str', v: (td.innerText || '').trim().toLowerCase() };
  }

  function comparePayload(a, b, asc) {
    if (a.kind === 'empty' && b.kind === 'empty') return 0;
    if (a.kind === 'empty') return 1;
    if (b.kind === 'empty') return -1;

    if (a.kind === 'num' && b.kind === 'num') {
      var na = Number.isNaN(a.v);
      var nb = Number.isNaN(b.v);
      if (na && nb) return 0;
      if (na) return 1;
      if (nb) return -1;
      return asc ? a.v - b.v : b.v - a.v;
    }
    var sa = a.kind === 'str' ? a.v : String(a.v);
    var sb = b.kind === 'str' ? b.v : String(b.v);
    var cmp = sa.localeCompare(sb, 'uk', { sensitivity: 'base', numeric: true });
    return asc ? cmp : -cmp;
  }

  function initTable(table) {
    var thead = table.tHead;
    if (!thead || !thead.rows.length) return;

    var headerRow = thead.rows[0];
    var ths = headerRow.cells;
    var sortState = { col: -1, asc: true };

    function clearIndicators() {
      for (var i = 0; i < ths.length; i++) {
        ths[i].classList.remove('js-sort-asc', 'js-sort-desc');
        ths[i].removeAttribute('aria-sort');
      }
    }

    for (var j = 0; j < ths.length; j++) {
      (function (th) {
        if (th.hasAttribute('data-sort-skip')) return;

        var sortType = th.getAttribute('data-sort-type') || 'num';
        th.classList.add('js-sort-header');
        if (!th.hasAttribute('tabindex')) th.setAttribute('tabindex', '0');
        th.setAttribute('role', 'columnheader');

        th.addEventListener('click', function () {
          var colIndex = th.cellIndex;
          if (sortState.col === colIndex) {
            sortState.asc = !sortState.asc;
          } else {
            sortState.col = colIndex;
            sortState.asc = true;
          }
          clearIndicators();
          th.classList.add(sortState.asc ? 'js-sort-asc' : 'js-sort-desc');
          th.setAttribute('aria-sort', sortState.asc ? 'ascending' : 'descending');

          var tbody = table.tBodies[0];
          if (!tbody) return;

          var rows = Array.prototype.slice.call(tbody.rows);
          var decorated = rows.map(function (tr) {
            var cell = tr.cells[colIndex];
            var p = cellSortPayload(cell, sortType);
            if (p.kind === 'str' && (!p.v || p.v === '—')) {
              p = { kind: 'empty', v: '' };
            }
            if (p.kind === 'num' && Number.isNaN(p.v)) {
              p = { kind: 'num', v: NaN };
            }
            return { tr: tr, p: p };
          });

          decorated.sort(function (x, y) {
            return comparePayload(x.p, y.p, sortState.asc);
          });

          var frag = document.createDocumentFragment();
          for (var k = 0; k < decorated.length; k++) {
            frag.appendChild(decorated[k].tr);
          }
          tbody.appendChild(frag);
        });

        th.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            th.click();
          }
        });
      })(ths[j]);
    }
  }

  function initAllSortTables() {
    document.querySelectorAll('table.js-sortable:not([data-sort-initialized])').forEach(function (table) {
      table.setAttribute('data-sort-initialized', '1');
      initTable(table);
    });
  }

  document.addEventListener('DOMContentLoaded', initAllSortTables);
  document.addEventListener('procurement-tables-updated', initAllSortTables);
})();
