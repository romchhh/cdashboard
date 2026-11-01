(function () {
  'use strict';

  var form = document.getElementById('mailStatsFilterForm');
  var fromInput = document.getElementById('statsFrom');
  var toInput = document.getElementById('statsTo');

  function formatDate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function applyPeriod(daysBack) {
    if (!fromInput || !toInput || !form) return;
    var today = new Date();
    var start = new Date(today);
    start.setDate(today.getDate() - daysBack);
    fromInput.value = formatDate(start);
    toInput.value = formatDate(today);
    form.submit();
  }

  document.querySelectorAll('[data-period-days]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var days = parseInt(btn.getAttribute('data-period-days') || '0', 10);
      if (days >= 0) applyPeriod(days);
    });
  });

  document.querySelectorAll('[data-period-month]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (!fromInput || !toInput || !form) return;
      var today = new Date();
      var start = new Date(today.getFullYear(), today.getMonth(), 1);
      fromInput.value = formatDate(start);
      toInput.value = formatDate(today);
      form.submit();
    });
  });
})();
