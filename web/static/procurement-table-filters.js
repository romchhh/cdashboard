/**
 * Серверні фільтри колонок таблиць закупівель:
 * debounce → GET-запит з page=1, параметри зберігаються в URL.
 */
(function () {
  'use strict';

  var DEBOUNCE_MS = 450;

  function initForm(form) {
    if (!form || form.dataset.procurementFiltersInit) return;
    form.dataset.procurementFiltersInit = '1';

    var pageInput = form.querySelector('input[name="page"]');
    var timer = null;

    function scheduleSubmit() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        if (pageInput) pageInput.value = '1';
        form.submit();
      }, DEBOUNCE_MS);
    }

    form.querySelectorAll('.procurement-col-filter').forEach(function (input) {
      input.addEventListener('input', scheduleSubmit);
      input.addEventListener('change', scheduleSubmit);
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (timer) clearTimeout(timer);
          if (pageInput) pageInput.value = '1';
          form.submit();
        }
      });
    });

    var resetBtn = form.querySelector('[data-procurement-filters-reset]');
    if (resetBtn) {
      resetBtn.addEventListener('click', function (e) {
        e.preventDefault();
        var base = form.getAttribute('action') || window.location.pathname;
        var keep = [];
        form.querySelectorAll('input[type="hidden"][data-keep-on-reset]').forEach(function (el) {
          if (el.name && el.value) {
            keep.push(encodeURIComponent(el.name) + '=' + encodeURIComponent(el.value));
          }
        });
        window.location.href = base + (keep.length ? '?' + keep.join('&') : '');
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form.procurement-table-filters-form').forEach(initForm);
  });
})();
