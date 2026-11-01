/**
 * Пошук у закупівлях:
 * - сторінки з data-procurement-server-search: debounced fetch по ВСЬОМУ каталогу розділу (сервер фільтрує до пагінації);
 * - data-procurement-client-search: миттєва фільтрація в DOM (усі рядки вже завантажені, напр. сторінка бренду).
 * Поле пошуку не в refresh-блоці — курсор не чіпаємо.
 */
(function () {
  'use strict';

  var SERVER_DEBOUNCE_MS = 450;
  var searchAbort = null;
  var debounceTimers = new WeakMap();

  function normalizeQuery(q) {
    return String(q || '').trim().toLowerCase();
  }

  function matchesHaystack(haystack, query) {
    var n = normalizeQuery(query);
    if (!n) return true;
    return String(haystack || '').indexOf(n) !== -1;
  }

  function skuTableForInput(input) {
    var sel = input.getAttribute('data-target-table');
    if (sel) {
      var t = document.querySelector(sel);
      if (t) return t;
    }
    var root = input.closest('[data-procurement-search-root]');
    if (root) {
      return root.querySelector('[data-procurement-sku-table]');
    }
    return null;
  }

  function filterSkuTableRows(input) {
    if (!input) return;
    var table = skuTableForInput(input);
    if (!table) return;
    var q = input.value;
    table.querySelectorAll('tbody tr[data-product-search]').forEach(function (row) {
      row.style.display = matchesHaystack(row.getAttribute('data-product-search'), q) ? '' : 'none';
    });
  }

  function bindClientSearchInput(input) {
    if (!input || input.dataset.procurementClientInit) return;
    input.dataset.procurementClientInit = '1';
    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', function () {
      filterSkuTableRows(input);
    });
  }

  function buildSearchUrl(form) {
    var pageInput = form.querySelector('input[name="page"]');
    if (pageInput) pageInput.value = '1';
    var params = new URLSearchParams(new FormData(form));
    params.forEach(function (_v, key) {
      if (params.get(key) === '') params.delete(key);
    });
    var qs = params.toString();
    var action = form.getAttribute('action') || window.location.pathname;
    return qs ? action + '?' + qs : action;
  }

  function replaceSearchRefresh(root, doc, url) {
    var oldBlock = root.querySelector('[data-procurement-search-refresh]');
    var newBlock = doc.querySelector('[data-procurement-search-refresh]');
    if (!oldBlock || !newBlock) {
      return false;
    }
    oldBlock.replaceWith(document.importNode(newBlock, true));
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, '', url);
    }
    document.dispatchEvent(new CustomEvent('procurement-tables-updated'));
    return true;
  }

  function fetchServerSearch(form) {
    var root = form.closest('[data-procurement-search-root]');
    if (!root) {
      form.submit();
      return;
    }

    var url = buildSearchUrl(form);

    if (searchAbort) {
      searchAbort.abort();
    }
    searchAbort = new AbortController();

    var refresh = root.querySelector('[data-procurement-search-refresh]');
    if (refresh) refresh.setAttribute('aria-busy', 'true');

    fetch(url, {
      signal: searchAbort.signal,
      credentials: 'same-origin',
      headers: { Accept: 'text/html' },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        if (!replaceSearchRefresh(root, doc, url)) {
          window.location.assign(url);
        }
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        window.location.assign(url);
      })
      .finally(function () {
        if (refresh) refresh.removeAttribute('aria-busy');
      });
  }

  function scheduleServerSearch(form, immediate) {
    var existing = debounceTimers.get(form);
    if (existing) {
      clearTimeout(existing);
      debounceTimers.delete(form);
    }
    if (immediate) {
      fetchServerSearch(form);
      return;
    }
    var timer = setTimeout(function () {
      debounceTimers.delete(form);
      fetchServerSearch(form);
    }, SERVER_DEBOUNCE_MS);
    debounceTimers.set(form, timer);
  }

  function bindServerSearchInput(form, input) {
    if (!input || input.dataset.procurementServerInput) return;
    input.dataset.procurementServerInput = '1';
    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', function () {
      scheduleServerSearch(form, false);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        scheduleServerSearch(form, true);
      }
    });
  }

  function initServerSearchForm(form) {
    if (!form || form.dataset.procurementServerInit) return;
    form.dataset.procurementServerInit = '1';

    var input = form.querySelector('input[name="q"]');
    bindServerSearchInput(form, input);

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      scheduleServerSearch(form, true);
    });
  }

  function boot() {
    document.querySelectorAll('form[data-procurement-server-search]').forEach(initServerSearchForm);
    document.querySelectorAll('input[data-procurement-client-search]').forEach(bindClientSearchInput);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.procurementProductSearch = {
    normalizeQuery: normalizeQuery,
    matchesHaystack: matchesHaystack,
    filterSkuTableRows: filterSkuTableRows,
  };
})();
