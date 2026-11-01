(function () {
  'use strict';

  var configEl = document.getElementById('mailingsPageConfig');
  if (!configEl) return;

  var config = {};
  try {
    config = JSON.parse(configEl.textContent || '{}');
  } catch (_e) {
    config = {};
  }

  var recipientsTotalFromServer = Number(config.recipientsTotal || 0) || 0;
  var eligibleRecipientsFromServer = Number(config.eligibleRecipients || 0) || 0;
  var stagedRecipientsKey = String(config.stagedRecipientsKey || '');
  var stagedRecipientsCount = Number(config.stagedRecipientsCount || 0) || 0;
  var fileTemplatesData = Array.isArray(config.templates) ? config.templates : [];
  var campaignsById = {};
  (Array.isArray(config.campaigns) ? config.campaigns : []).forEach(function (c) {
    if (c && c.id != null) campaignsById[String(c.id)] = c;
  });
  var isTouchUi = (function () {
    try {
      return window.matchMedia('(hover: none) and (pointer: coarse)').matches
        || (navigator.maxTouchPoints || 0) > 1;
    } catch (_e) {
      return false;
    }
  })();

  var body = document.getElementById('mailBody');
  var bodyHidden = document.getElementById('mailBodyHidden');
  var isHtml = document.getElementById('isHtml');
  var preview = document.getElementById('mailPreview');
  var targetMode = document.getElementById('targetMode');
  var mailingsForm = document.getElementById('mailingsForm');
  var customRecipientsInput = document.getElementById('customRecipients');
  var subjectInput = document.getElementById('mailSubject');
  var fileTemplateSelect = document.getElementById('fileTemplateSelect');
  var templateHint = document.getElementById('templateHint');
  var recipientsFileInput = document.getElementById('recipientsFile');
  var recipientsDropzone = document.getElementById('recipientsDropzone');
  var recipientsFileName = document.getElementById('recipientsFileName');
  var recipientsFileClear = document.getElementById('recipientsFileClear');
  var recipientCountBadge = document.getElementById('recipientCountBadge');
  var subjectCountHint = document.getElementById('subjectCountHint');
  var enableVisualEdit = document.getElementById('enableVisualEdit');
  var visualEditControls = document.getElementById('visualEditControls');
  var visualDropHint = document.getElementById('visualDropHint');
  var saveNewTemplateBtn = document.getElementById('saveNewTemplateBtn');
  var visualLinkInput = document.getElementById('visualLinkInput');
  var applyVisualLinkBtn = document.getElementById('applyVisualLinkBtn');
  var removeVisualLinkBtn = document.getElementById('removeVisualLinkBtn');
  var sendBtn = document.getElementById('mailSendBtn');
  var confirmDialog = document.getElementById('mailConfirmDialog');
  var confirmTitle = document.getElementById('mailConfirmTitle');
  var confirmLead = document.getElementById('mailConfirmLead');
  var confirmMeta = document.getElementById('mailConfirmMeta');
  var confirmOkBtn = document.getElementById('mailConfirmOk');
  var confirmCancelBtn = document.getElementById('mailConfirmCancel');
  var noticeDialog = document.getElementById('mailNoticeDialog');
  var noticeTitle = document.getElementById('mailNoticeTitle');
  var noticeText = document.getElementById('mailNoticeText');
  var mailingInFlight = false;
  var mailingFetchStarted = false;
  var confirmSession = null;
  var mailActions = document.querySelector('.mail-actions');
  var mailBodyDrop = document.getElementById('mailBodyDrop');
  var mailTemplateNameInput = document.getElementById('mailTemplateName');
  var mailPreviewStatus = document.getElementById('mailPreviewStatus');
  var loadedHtmlFileName = '';
  var canonicalMailHtml = '';
  var visualEditMode = false;
  var selectedElement = null;

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeHtmlContent(html) {
    return String(html || '').replace(/^\uFEFF/, '');
  }

  function looksLikeHtml(value) {
    return /<[a-z][\s\S]*?>/i.test(String(value || ''));
  }

  function extractPreviewHtml(html) {
    var s = normalizeHtmlContent(html).trim();
    if (!s) return '';
    if (!/<html[\s>]/i.test(s)) return s;
    try {
      var doc = new DOMParser().parseFromString(s, 'text/html');
      var styles = '';
      doc.querySelectorAll('style').forEach(function (node) {
        styles += node.outerHTML;
      });
      var bodyHtml = doc.body ? doc.body.innerHTML : '';
      if (bodyHtml.trim()) return styles + bodyHtml;
    } catch (_e) { /* ignore */ }
    return s;
  }

  function updatePreviewStatus(text, ready) {
    if (!mailPreviewStatus) return;
    mailPreviewStatus.textContent = text || '';
    mailPreviewStatus.style.color = ready ? '#059669' : '#64748b';
  }

  var previewRenderTimer = null;

  function clearHeavyPreview(reason) {
    if (previewRenderTimer) {
      clearTimeout(previewRenderTimer);
      previewRenderTimer = null;
    }
    if (!preview) return;
    preview.classList.remove('has-iframe', 'visual-edit-mode');
    preview.innerHTML =
      '<p class="mail-preview-empty">' +
      escapeHtml(reason || 'Превʼю приховано на час розсилки.') +
      '</p>';
    updatePreviewStatus(reason || 'Превʼю на паузі', false);
  }

  function renderPreview() {
    if (!preview || !body || !isHtml) return;
    if (document.body.classList.contains('mailing-active')) {
      clearHeavyPreview('Превʼю приховано на час розсилки');
      return;
    }
    var value = normalizeHtmlContent(body.value);
    if (!value.trim()) {
      preview.classList.remove('has-iframe', 'visual-edit-mode');
      preview.innerHTML = '<p class="mail-preview-empty">Введіть HTML у поле вище або перетягніть .html файл — тут з’явиться перегляд.</p>';
      updatePreviewStatus('Введіть код або перетягніть HTML-файл', false);
      return;
    }

    var asHtml = isHtml.checked || looksLikeHtml(value);
    if (asHtml && !isHtml.checked) isHtml.checked = true;

    if (!asHtml) {
      preview.classList.remove('has-iframe');
      preview.innerHTML = '<pre style="white-space:pre-wrap;margin:0;font-family:inherit;">' + escapeHtml(value) + '</pre>';
      updatePreviewStatus('Текстовий режим', true);
      return;
    }

    if (visualEditMode) {
      preview.classList.remove('has-iframe');
      preview.innerHTML = '<div class="mail-preview-html-root">' + extractPreviewHtml(value) + '</div>';
      attachVisualEditableElements();
      updatePreviewStatus('Візуальний редактор', true);
      return;
    }

    preview.classList.add('has-iframe');
    var frame = preview.querySelector('iframe.mail-preview-frame');
    if (!frame) {
      preview.innerHTML = '';
      frame = document.createElement('iframe');
      frame.className = 'mail-preview-frame';
      frame.setAttribute('title', 'Превʼю листа');
      frame.setAttribute('sandbox', 'allow-same-origin');
      preview.appendChild(frame);
    }
    // Reuse iframe — assigning srcdoc alone is cheaper than tear-down on iPad.
    if (frame.srcdoc !== value) frame.srcdoc = value;
    updatePreviewStatus(loadedHtmlFileName ? ('Готово: ' + loadedHtmlFileName) : 'HTML-перегляд', true);
  }

  function schedulePreviewRender() {
    if (document.body.classList.contains('mailing-active')) return;
    if (previewRenderTimer) clearTimeout(previewRenderTimer);
    previewRenderTimer = setTimeout(function () {
      previewRenderTimer = null;
      renderPreview();
    }, isTouchUi ? 450 : 180);
  }

  function isPreviewIframeMarkup(value) {
    var s = String(value || '').trim();
    return /^<iframe\b/i.test(s) && /mail-preview-frame/i.test(s);
  }

  function rememberMailHtml(value) {
    var content = normalizeHtmlContent(value).trim();
    if (content && !isPreviewIframeMarkup(content)) {
      canonicalMailHtml = content;
    }
    return content;
  }

  function getMailHtmlForSubmit() {
    if (!body) return canonicalMailHtml || '';
    if (visualEditMode && preview) {
      var root = preview.querySelector('.mail-preview-html-root');
      return (root ? root.innerHTML : preview.innerHTML).trim() || canonicalMailHtml || '';
    }
    var current = normalizeHtmlContent(body.value).trim();
    if ((!current || isPreviewIframeMarkup(current)) && canonicalMailHtml) {
      return canonicalMailHtml;
    }
    return rememberMailHtml(current) || current || canonicalMailHtml || '';
  }

  function syncBodyFieldForSubmit() {
    var html = getMailHtmlForSubmit();
    if (bodyHidden) bodyHidden.value = html;
    if (body && html && body.value !== html && !visualEditMode) {
      body.value = html;
    }
    if (isHtml && html && looksLikeHtml(html)) isHtml.checked = true;
  }

  function syncPreviewToTextarea() {
    if (!preview || !body || !visualEditMode) return;
    var root = preview.querySelector('.mail-preview-html-root');
    var html = (root ? root.innerHTML : preview.innerHTML).trim();
    body.value = html;
    rememberMailHtml(html);
    if (bodyHidden) bodyHidden.value = html;
  }

  function clearSelection() {
    if (selectedElement && selectedElement.classList) selectedElement.classList.remove('ve-selected');
    selectedElement = null;
    if (visualLinkInput) visualLinkInput.value = '';
  }

  function setSelectedElement(el) {
    if (!el) return;
    clearSelection();
    selectedElement = el;
    selectedElement.classList.add('ve-selected');
    syncLinkInputWithSelection();
  }

  function closestLinkForElement(el) {
    if (!el) return null;
    if (el.tagName === 'A') return el;
    return el.closest ? el.closest('a') : null;
  }

  function syncLinkInputWithSelection() {
    if (!visualLinkInput) return;
    var link = closestLinkForElement(selectedElement);
    visualLinkInput.value = link ? (link.getAttribute('href') || '') : '';
  }

  function applyLinkToSelectedElement() {
    if (!visualEditMode) {
      alert('Увімкніть візуальний редактор.');
      return;
    }
    if (!selectedElement) {
      alert('Спочатку оберіть блок у попередньому перегляді.');
      return;
    }
    var href = String((visualLinkInput && visualLinkInput.value) || '').trim();
    if (!href) {
      alert('Вкажіть посилання (URL).');
      return;
    }
    var link = closestLinkForElement(selectedElement);
    if (link) {
      link.setAttribute('href', href);
      syncPreviewToTextarea();
      return;
    }
    var wrapper = document.createElement('a');
    wrapper.setAttribute('href', href);
    if (selectedElement.parentNode) {
      selectedElement.parentNode.insertBefore(wrapper, selectedElement);
      wrapper.appendChild(selectedElement);
      clearSelection();
      setSelectedElement(wrapper);
      syncPreviewToTextarea();
    }
  }

  function selectedImageTarget() {
    if (!selectedElement) return null;
    if (selectedElement.tagName === 'IMG') return { type: 'img', node: selectedElement };
    var bgUrl = extractBackgroundImageUrl(selectedElement);
    if (bgUrl) return { type: 'bg', node: selectedElement };
    var nestedImg = selectedElement.querySelector && selectedElement.querySelector('img');
    if (nestedImg) return { type: 'img', node: nestedImg };
    return null;
  }

  function uploadImageForSelectedElement(file) {
    if (!visualEditMode) {
      alert('Увімкніть візуальний редактор.');
      return Promise.reject(new Error('visual edit off'));
    }
    var target = selectedImageTarget();
    if (!target) {
      alert('Оберіть блок із зображенням або фоновим фото у превʼю.');
      return Promise.reject(new Error('no image target'));
    }
    if (!file) {
      alert('Не вдалося отримати файл зображення.');
      return Promise.reject(new Error('no file'));
    }

    var form = new FormData();
    form.append('image_file', file);
    var csrfInput = document.querySelector('input[name="csrf_token"]');
    var csrfToken = csrfInput ? csrfInput.value : '';
    if (csrfToken) form.append('csrf_token', csrfToken);
    var headers = {};
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;

    return fetch('/clients/mailings/upload-image', {
      method: 'POST',
      headers: headers,
      body: form
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data || !data.success || !data.url) {
          throw new Error((data && data.error) || 'Не вдалося завантажити файл.');
        }
        var mediaUrl = String(data.absolute_url || data.url || '').trim();
        if (!mediaUrl) throw new Error('Сервер не повернув URL зображення.');
        if (target.type === 'img') {
          target.node.setAttribute('src', mediaUrl);
        } else {
          target.node.style.backgroundImage = 'url("' + mediaUrl.replace(/"/g, '%22') + '")';
        }
        syncPreviewToTextarea();
        return data;
      });
  }

  function removeLinkFromSelectedElement() {
    if (!visualEditMode) {
      alert('Увімкніть візуальний редактор.');
      return;
    }
    if (!selectedElement) {
      alert('Спочатку оберіть блок у попередньому перегляді.');
      return;
    }
    var link = closestLinkForElement(selectedElement);
    if (!link) {
      if (visualLinkInput) visualLinkInput.value = '';
      return;
    }
    var parent = link.parentNode;
    if (!parent) return;
    while (link.firstChild) parent.insertBefore(link.firstChild, link);
    parent.removeChild(link);
    clearSelection();
    syncPreviewToTextarea();
  }

  function isTextElement(node) {
    return !!node && /^(P|H1|H2|H3|H4|H5|H6|SPAN|DIV|LI|TD|TH|A|BUTTON)$/.test(node.tagName);
  }

  function hasBackgroundImage(node) {
    return !!extractBackgroundImageUrl(node);
  }

  function findBackgroundEditableFrom(startNode) {
    if (!preview || !startNode) return null;
    var node = startNode;
    while (node && node !== preview) {
      if (hasBackgroundImage(node)) return node;
      node = node.parentElement;
    }
    return hasBackgroundImage(preview) ? preview : null;
  }

  function findImgInRelativeContainer(startNode) {
    if (!preview || !startNode) return null;
    var node = startNode;
    while (node && node !== preview) {
      var st = node.getAttribute ? (node.getAttribute('style') || '') : '';
      if (/position\s*:\s*relative/i.test(st)) {
        var img = node.querySelector('img');
        if (img) return img;
      }
      node = node.parentElement;
    }
    return null;
  }

  function attachVisualEditableElements() {
    if (!preview) return;
    preview.querySelectorAll('.ve-editable').forEach(function (el) {
      el.classList.remove('ve-editable');
      el.removeAttribute('contenteditable');
      el.removeAttribute('data-ve-img-block');
    });

    preview.querySelectorAll('*').forEach(function (el) {
      if (el.closest('script,style')) return;
      if (el.tagName === 'IMG') {
        el.classList.add('ve-editable');
      } else if (hasBackgroundImage(el)) {
        el.classList.add('ve-editable');
      } else {
        var st = el.getAttribute ? (el.getAttribute('style') || '') : '';
        if (/position\s*:\s*relative/i.test(st) && el.querySelector('img')) {
          el.classList.add('ve-editable');
          el.setAttribute('data-ve-img-block', '1');
        } else if (isTextElement(el)) {
          el.classList.add('ve-editable');
          el.setAttribute('contenteditable', 'true');
        }
      }
    });
  }

  function extractBackgroundImageUrl(el) {
    if (!el) return '';
    var bg = '';
    if (el.style && el.style.backgroundImage) bg = el.style.backgroundImage;
    if (!bg && window.getComputedStyle) bg = window.getComputedStyle(el).backgroundImage || '';
    var raw = String(bg || '').trim();
    if (!raw || raw === 'none') return '';
    var match = raw.match(/url\((['"]?)(.*?)\1\)/i);
    return match ? String(match[2] || '').trim() : '';
  }

  function openBackgroundQuickEdit(el) {
    if (!el) return;
    var currentUrl = extractBackgroundImageUrl(el);
    if (!currentUrl) return;
    var newUrl = prompt('Вставте нове посилання на фонове фото (URL):', currentUrl);
    if (newUrl === null) return;
    var normalized = String(newUrl || '').trim();
    if (!normalized) {
      alert('Посилання на фонове фото не може бути порожнім.');
      return;
    }
    if (isRiskyExternalImageUrl(normalized)) {
      alert('Це тимчасове посилання (Instagram CDN), воно може зникнути в листах. Краще використати стабільне зображення з вашого сайту, напр. /static/....');
    }
    el.style.backgroundImage = 'url("' + normalized.replace(/"/g, '%22') + '")';
    syncPreviewToTextarea();
  }

  function isRiskyExternalImageUrl(url) {
    var value = String(url || '').trim().toLowerCase();
    if (!value) return false;
    if (value.indexOf('cdninstagram.com') !== -1) return true;
    if (value.indexOf('_nc_') !== -1) return true;
    if (value.indexOf('oh=') !== -1 && value.indexOf('oe=') !== -1) return true;
    return false;
  }

  function enableVisualEditing() {
    if (!isHtml.checked) {
      alert('Візуальний редактор працює лише для HTML-листів. Увімкніть "Лист у HTML".');
      if (enableVisualEdit) enableVisualEdit.checked = false;
      return;
    }
    preview.classList.add('visual-edit-mode');
    if (visualEditControls) visualEditControls.style.display = 'grid';
    if (visualDropHint) visualDropHint.classList.add('active');
    attachVisualEditableElements();
  }

  function disableVisualEditing() {
    preview.classList.remove('visual-edit-mode');
    if (visualEditControls) visualEditControls.style.display = 'none';
    if (visualDropHint) visualDropHint.classList.remove('active', 'dragging');
    clearSelection();
    preview.querySelectorAll('.ve-editable').forEach(function (node) {
      node.classList.remove('ve-editable');
      node.removeAttribute('contenteditable');
    });
  }

  function toggleCustomRecipients() {
    if (!targetMode) return;
    var isCustom = targetMode.value === 'custom';
    var customRecipientsBlock = document.getElementById('customRecipientsBlock');
    if (customRecipientsBlock) customRecipientsBlock.classList.toggle('active', isCustom);
    updateRecipientCountBadge();
  }

  function updateTemplateHint() {
    if (!fileTemplateSelect || !templateHint) return;
    var selectedName = String(fileTemplateSelect.value || '');
    if (!selectedName) {
      templateHint.textContent = 'Оберіть готовий шаблон або напишіть лист вручну.';
      return;
    }
    var tpl = fileTemplatesData.find(function (x) { return String(x.name || '') === selectedName; });
    templateHint.textContent = tpl
      ? 'Тема шаблону: «' + (tpl.subject || tpl.label || selectedName) + '»'
      : '';
  }

  function applyLoadedTemplate(tpl) {
    if (!tpl) return;
    if (visualEditMode) {
      if (enableVisualEdit) enableVisualEdit.checked = false;
      visualEditMode = false;
      disableVisualEditing();
    }
    if (subjectInput && !String(subjectInput.value || '').trim()) subjectInput.value = tpl.subject || '';
    if (body) body.value = tpl.html || '';
    rememberMailHtml(tpl.html || '');
    if (isHtml) isHtml.checked = true;
    loadedHtmlFileName = '';
    updateSubjectCount();
    updateTemplateHint();
    renderPreview();
  }

  function applyFileTemplate() {
    if (!fileTemplateSelect) return;
    var selectedName = String(fileTemplateSelect.value || '');
    if (!selectedName) {
      updateTemplateHint();
      return;
    }
    var tpl = fileTemplatesData.find(function (x) { return String(x.name || '') === selectedName; });
    if (!tpl) return;
    if (tpl.html) {
      applyLoadedTemplate(tpl);
      return;
    }
    if (templateHint) templateHint.textContent = 'Завантаження шаблону…';
    fetch('/clients/mailings/templates/' + encodeURIComponent(selectedName), {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Не вдалося завантажити шаблон');
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.ok) throw new Error((data && data.error) || 'Шаблон недоступний');
        tpl.html = data.html || '';
        if (data.subject) tpl.subject = data.subject;
        applyLoadedTemplate(tpl);
      })
      .catch(function (err) {
        if (templateHint) {
          templateHint.textContent = (err && err.message) ? err.message : 'Помилка завантаження шаблону';
        }
      });
  }

  function saveNewTemplate() {
    if (!body || !body.value.trim()) {
      alert('Спочатку напишіть HTML-лист.');
      return;
    }
    if (!isHtml || !isHtml.checked) {
      alert('Шаблон можна зберегти лише для HTML-листів.');
      return;
    }
    var templateName = prompt('Введіть назву нового шаблону (без розширення .html):');
    if (!templateName || !templateName.trim()) return;
    templateName = templateName.trim().replace(/[^a-zA-Z0-9_-]/g, '_');
    if (!templateName.endsWith('.html')) templateName += '.html';

    fetch('/clients/mailings/save-template', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': document.querySelector('input[name="csrf_token"]').value
      },
      body: JSON.stringify({
        name: templateName,
        subject: subjectInput.value.trim() || 'Новий шаблон',
        html: body.value
      })
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.success) {
          alert('Шаблон "' + templateName + '" успішно збережено!');
          location.reload();
        } else {
          alert('Помилка: ' + (data.error || 'Невідома помилка'));
        }
      })
      .catch(function (error) {
        alert('Не вдалося зберегти шаблон: ' + error.message);
      });
  }

  var fileEmailCount = 0;
  var fileEmailCountPending = false;
  var fileCountRequestId = 0;

  function countEmailsInText(text) {
    var matches = String(text || '').match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [];
    var uniq = {};
    for (var i = 0; i < matches.length; i += 1) {
      uniq[String(matches[i] || '').trim().toLowerCase()] = true;
    }
    return Object.keys(uniq).filter(function (x) { return !!x; }).length;
  }

  function getCsrfToken() {
    var el = mailingsForm && mailingsForm.querySelector('input[name="csrf_token"]');
    return el ? String(el.value || '') : '';
  }

  function refreshFileEmailCount() {
    if (!recipientsFileInput) {
      fileEmailCount = 0;
      updateRecipientCountBadge();
      return;
    }
    var file = recipientsFileInput.files && recipientsFileInput.files[0];
    if (!file) {
      fileEmailCount = 0;
      fileEmailCountPending = false;
      updateRecipientCountBadge();
      return;
    }

    var name = String(file.name || '').toLowerCase();
    var reqId = ++fileCountRequestId;
    fileEmailCountPending = true;
    updateRecipientCountBadge();

    // CSV / TXT — швидко в браузері (експорт клієнтів з «;»)
    if (name.endsWith('.csv') || name.endsWith('.txt') || (file.type && file.type.indexOf('csv') !== -1)) {
      var reader = new FileReader();
      reader.onload = function () {
        if (reqId !== fileCountRequestId) return;
        fileEmailCount = countEmailsInText(reader.result);
        fileEmailCountPending = false;
        updateRecipientCountBadge();
      };
      reader.onerror = function () {
        if (reqId !== fileCountRequestId) return;
        // fallback на сервер
        countFileEmailsOnServer(file, reqId);
      };
      reader.readAsText(file);
      return;
    }

    countFileEmailsOnServer(file, reqId);
  }

  function countFileEmailsOnServer(file, reqId) {
    var fd = new FormData();
    fd.append('csrf_token', getCsrfToken());
    fd.append('recipients_file', file);
    if (customRecipientsInput) fd.append('custom_recipients', customRecipientsInput.value || '');
    fetch('/clients/mailings/count-file-emails', {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (reqId !== fileCountRequestId) return;
        if (res.data && res.data.ok) {
          fileEmailCount = Number(res.data.from_file || 0);
        } else {
          fileEmailCount = 0;
        }
        fileEmailCountPending = false;
        updateRecipientCountBadge();
      })
      .catch(function () {
        if (reqId !== fileCountRequestId) return;
        fileEmailCount = 0;
        fileEmailCountPending = false;
        updateRecipientCountBadge();
      });
  }

  function showSelectedFileName() {
    if (!recipientsFileInput || !recipientsFileName) return;
    var file = recipientsFileInput.files && recipientsFileInput.files[0];
    recipientsFileName.textContent = file ? file.name : '';
    if (recipientsFileClear) {
      recipientsFileClear.hidden = !file;
      recipientsFileClear.setAttribute('aria-hidden', file ? 'false' : 'true');
    }
    refreshFileEmailCount();
  }

  function clearRecipientsFile() {
    if (!recipientsFileInput) return;
    recipientsFileInput.value = '';
    fileEmailCount = 0;
    fileEmailCountPending = false;
    showSelectedFileName();
  }

  function estimateCustomRecipientsCount() {
    return countEmailsInText((customRecipientsInput && customRecipientsInput.value) || '');
  }

  function estimateTotalCustomRecipients() {
    // приблизна сума (без повної дедуплікації між полем і файлом у браузері)
    return estimateCustomRecipientsCount() + (fileEmailCount || 0) + (stagedRecipientsCount || 0);
  }

  function updateRecipientCountBadge() {
    if (!recipientCountBadge || !targetMode) return;
    var mode = String(targetMode.value || 'all');
    var label = '';
    if (mode === 'all') {
      label = 'Отримувачів: ' + recipientsTotalFromServer + ' (усі з бази)';
    } else {
      var manualCount = estimateCustomRecipientsCount();
      var stagedCount = stagedRecipientsCount || 0;
      var hasFile = !!(recipientsFileInput && recipientsFileInput.files && recipientsFileInput.files.length);
      if (hasFile && fileEmailCountPending) {
        label = 'Отримувачів: рахуємо з файлу…';
      } else if (hasFile) {
        var total = manualCount + fileEmailCount + stagedCount;
        label = 'Отримувачів: ~' + total;
        var bits = [];
        if (stagedCount) bits.push('фільтр ' + stagedCount);
        if (manualCount) bits.push('поле ' + manualCount);
        if (fileEmailCount) bits.push('файл ' + fileEmailCount);
        if (bits.length) label += ' (' + bits.join(' + ') + ')';
      } else if (stagedCount && manualCount) {
        label = 'Отримувачів: ~' + (stagedCount + manualCount) + ' (фільтр ' + stagedCount + ' + поле ' + manualCount + ')';
      } else if (stagedCount) {
        label = 'Отримувачів: ' + stagedCount + ' (з фільтрів клієнтів)';
      } else {
        label = 'Отримувачів: ' + manualCount + ' (кастомний список)';
      }
    }
    recipientCountBadge.textContent = label;
  }

  function updateSubjectCount() {
    if (!subjectInput || !subjectCountHint) return;
    var len = String(subjectInput.value || '').length;
    subjectCountHint.textContent = len + ' / 500';
    subjectCountHint.classList.toggle('warn', len > 500);
  }

  function readFileAsText(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || '')); };
      reader.onerror = function () { reject(new Error('Не вдалося прочитати файл.')); };
      reader.readAsText(file, 'UTF-8');
    });
  }

  function templateLabelFromFileName(fileName) {
    return String(fileName || '')
      .replace(/\.(html?|htm)$/i, '')
      .replace(/[_-]+/g, ' ')
      .trim();
  }

  function disableVisualEditIfNeeded() {
    if (!visualEditMode) return;
    if (enableVisualEdit) enableVisualEdit.checked = false;
    visualEditMode = false;
    disableVisualEditing();
  }

  function applyHtmlTemplateFile(file) {
    if (!file || !body) return Promise.resolve();

    function finishLoad(html) {
      var content = normalizeHtmlContent(html).trim();
      if (!content) {
        alert('Файл порожній або не містить тексту.');
        return;
      }
      if (!looksLikeHtml(content)) {
        alert('Файл «' + (file.name || 'без назви') + '» не схожий на HTML-лист.');
        return;
      }
      disableVisualEditIfNeeded();
      body.value = content;
      rememberMailHtml(content);
      loadedHtmlFileName = file.name || '';
      if (isHtml) isHtml.checked = true;
      if (fileTemplateSelect) fileTemplateSelect.value = '';
      if (mailTemplateNameInput && !String(mailTemplateNameInput.value || '').trim()) {
        mailTemplateNameInput.value = templateLabelFromFileName(file.name);
      }
      updateTemplateHint();
      updateSubjectCount();
      renderPreview();
    }

    return readFileAsText(file)
      .then(finishLoad)
      .catch(function (err) {
        alert('Помилка читання файлу: ' + ((err && err.message) || 'невідома помилка'));
      });
  }

  function initMailBodyDrop() {
    if (!mailBodyDrop || !body) return;

    var dragDepth = 0;

    function setDragActive(on) {
      mailBodyDrop.classList.toggle('dragover', on);
    }

    ['dragenter', 'dragover'].forEach(function (eventName) {
      mailBodyDrop.addEventListener(eventName, function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (eventName === 'dragenter') dragDepth += 1;
        setDragActive(true);
      });
    });

    mailBodyDrop.addEventListener('dragleave', function (e) {
      e.preventDefault();
      e.stopPropagation();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) setDragActive(false);
    });

    mailBodyDrop.addEventListener('drop', function (e) {
      e.preventDefault();
      e.stopPropagation();
      dragDepth = 0;
      setDragActive(false);
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      applyHtmlTemplateFile(files[0]);
    });

    body.addEventListener('dragover', function (e) {
      e.preventDefault();
    });
  }

  function setSubmitButtonBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = !!busy;
    btn.classList.toggle('is-loading', !!busy);
    btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  }

  function setAllMailingSubmitButtonsDisabled(disabled) {
    if (sendBtn) {
      sendBtn.disabled = disabled;
      if (!disabled) sendBtn.classList.remove('is-loading');
    }
    document.querySelectorAll('.js-resend-form button[type="submit"]').forEach(function (btn) {
      btn.disabled = disabled;
      if (!disabled) btn.classList.remove('is-loading');
    });
  }

  function showMailNotice(message, title) {
    if (!noticeDialog) {
      window.alert(message);
      return;
    }
    if (noticeTitle) noticeTitle.textContent = title || 'Увага';
    if (noticeText) noticeText.textContent = message || '';
    noticeDialog.hidden = false;
  }

  function closeMailNotice() {
    if (noticeDialog) noticeDialog.hidden = true;
  }

  function closeMailConfirm(restoreTrigger) {
    if (confirmDialog) confirmDialog.hidden = true;
    if (confirmOkBtn) confirmOkBtn.disabled = false;
    if (restoreTrigger && confirmSession && confirmSession.triggerEl) {
      setSubmitButtonBusy(confirmSession.triggerEl, false);
    }
    confirmSession = null;
  }

  function openMailConfirm(cfg) {
    cfg = cfg || {};
    if (!confirmDialog) {
      if (window.confirm((cfg.fallbackText) || 'Підтвердити відправку?')) {
        if (cfg.onConfirm) cfg.onConfirm();
      } else if (cfg.onCancel) {
        cfg.onCancel();
      }
      return;
    }
    confirmSession = cfg;
    if (confirmTitle) confirmTitle.textContent = cfg.title || 'Підтвердити відправку?';
    if (confirmLead) {
      confirmLead.textContent = cfg.lead || 'Перевірте дані перед запуском розсилки.';
      confirmLead.hidden = !cfg.lead;
    }
    if (confirmMeta) {
      confirmMeta.innerHTML = '';
      (cfg.meta || []).forEach(function (row) {
        if (!row || !row.label) return;
        var dt = document.createElement('dt');
        dt.textContent = row.label;
        var dd = document.createElement('dd');
        dd.textContent = row.value || '—';
        confirmMeta.appendChild(dt);
        confirmMeta.appendChild(dd);
      });
    }
    if (confirmOkBtn) confirmOkBtn.textContent = cfg.confirmLabel || 'Так, надіслати';
    if (confirmCancelBtn) confirmCancelBtn.hidden = !!cfg.hideCancel;
    confirmDialog.hidden = false;
    if (confirmOkBtn) confirmOkBtn.focus();
  }

  function initMailDialogs() {
    if (confirmDialog) {
      confirmDialog.querySelectorAll('[data-mail-dialog-cancel]').forEach(function (el) {
        el.addEventListener('click', function () {
          if (confirmSession && confirmSession.onCancel) confirmSession.onCancel();
          closeMailConfirm(true);
        });
      });
      if (confirmOkBtn) {
        confirmOkBtn.addEventListener('click', function () {
          if (confirmOkBtn.disabled) return;
          confirmOkBtn.disabled = true;
          var session = confirmSession;
          closeMailConfirm(false);
          if (session && session.onConfirm) session.onConfirm();
        });
      }
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || confirmDialog.hidden) return;
        if (confirmSession && confirmSession.onCancel) confirmSession.onCancel();
        closeMailConfirm(true);
      });
    }
    if (noticeDialog) {
      noticeDialog.querySelectorAll('[data-mail-notice-close]').forEach(function (el) {
        el.addEventListener('click', closeMailNotice);
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && noticeDialog && !noticeDialog.hidden) closeMailNotice();
      });
    }
  }

  function confirmBeforeSending(e) {
    e.preventDefault();
    if (!mailingsForm) return;
    if (mailingInFlight) return;
    if (visualEditMode) syncPreviewToTextarea();
    syncBodyFieldForSubmit();
    var htmlForSend = bodyHidden ? String(bodyHidden.value || '').trim() : getMailHtmlForSubmit();
    if (!htmlForSend) {
      showMailNotice('HTML листа порожній. Вставте код або перетягніть .html файл.', 'Неможливо надіслати');
      return;
    }
    if (isPreviewIframeMarkup(htmlForSend)) {
      showMailNotice(
        'Для відправки потрібен HTML-код листа, а не превʼю. Оновіть сторінку (Ctrl+F5) і спробуйте знову.',
        'Неможливо надіслати'
      );
      return;
    }
    var subject = String((subjectInput && subjectInput.value) || '').trim() || '(без теми)';
    var mode = String((targetMode && targetMode.value) || 'all');
    var recipientsLabel = '';
    if (mode === 'all') {
      recipientsLabel = String(recipientsTotalFromServer) + ' (усім з бази)';
    } else {
      var manualCount = estimateCustomRecipientsCount();
      var stagedCount = stagedRecipientsCount || 0;
      var hasFile = !!(recipientsFileInput && recipientsFileInput.files && recipientsFileInput.files.length);
      if (hasFile && fileEmailCountPending) {
        showMailNotice('Зачекайте — ще рахуємо email у файлі.', 'Зачекайте');
        return;
      }
      if (hasFile) {
        var totalApprox = manualCount + fileEmailCount + stagedCount;
        if (totalApprox <= 0) {
          showMailNotice(
            'У файлі та полі не знайдено жодної email-адреси. Перевірте, що у CSV є колонка Email.',
            'Немає отримувачів'
          );
          return;
        }
        recipientsLabel = '~' + totalApprox;
        var labelBits = [];
        if (stagedCount) labelBits.push('фільтр ' + stagedCount);
        if (manualCount) labelBits.push('поле ' + manualCount);
        if (fileEmailCount) labelBits.push('файл ' + fileEmailCount);
        if (labelBits.length) recipientsLabel += ' (' + labelBits.join(' + ') + ')';
      } else if (stagedCount || manualCount > 0) {
        if (stagedCount && manualCount) {
          recipientsLabel = '~' + (stagedCount + manualCount) + ' (фільтр ' + stagedCount + ' + поле ' + manualCount + ')';
        } else if (stagedCount) {
          recipientsLabel = String(stagedCount) + ' (з фільтрів клієнтів)';
        } else {
          recipientsLabel = String(manualCount) + ' (кастомний список)';
        }
      } else {
        showMailNotice('Додайте email у поле або завантажте файл.', 'Немає отримувачів');
        return;
      }
    }
    setSubmitButtonBusy(sendBtn, true);
    openMailConfirm({
      title: 'Надіслати розсилку?',
      lead: 'Після підтвердження запуск відбудеться одразу. Повторний клік не потрібен.',
      meta: [
        { label: 'Тема', value: subject },
        { label: 'Отримувачі', value: recipientsLabel }
      ],
      triggerEl: sendBtn,
      fallbackText:
        'Підтвердіть відправку розсилки:\n\nТема: ' + subject + '\nОтримувачі: ' + recipientsLabel,
      onCancel: function () {},
      onConfirm: function () {
        if (mailingInFlight) return;
        mailingInFlight = true;
        setSubmitButtonBusy(sendBtn, true);
        setAllMailingSubmitButtonsDisabled(true);
        startAsyncMailing(mailingsForm, { subject: subject });
      }
    });
  }

  var progressRoot = document.getElementById('mailSendProgress');
  var progressSubject = document.getElementById('mailProgressSubject');
  var progressBar = document.getElementById('mailProgressBar');
  var progressBarWrap = document.getElementById('mailProgressBarWrap');
  var progressStats = document.getElementById('mailProgressStats');
  var progressMessage = document.getElementById('mailProgressMessage');
  var progressActions = document.getElementById('mailProgressActions');
  var progressTitle = progressRoot ? progressRoot.querySelector('.mail-progress-title') : null;
  var progressReload = document.getElementById('mailProgressReload');
  var progressClose = document.getElementById('mailProgressClose');
  var progressHide = document.getElementById('mailProgressHide');
  var progressStop = document.getElementById('mailProgressStop');
  var progressLiveActions = document.getElementById('mailProgressLiveActions');
  var jobBanner = document.getElementById('mailJobBanner');
  var jobBannerText = document.getElementById('mailJobBannerText');
  var jobBannerShow = document.getElementById('mailJobBannerShow');
  var pollTimer = null;
  var activeJobId = null;
  var pollFailStreak = 0;
  var pollAbort = null;
  var stopInFlight = false;
  // iPad Safari: rarer polls + abort previous request to avoid queue stalls.
  var POLL_MS_FOREGROUND = isTouchUi ? 4000 : 2000;
  var POLL_MS_BACKGROUND = isTouchUi ? 12000 : 8000;

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (pollAbort) {
      try { pollAbort.abort(); } catch (_e) {}
      pollAbort = null;
    }
    activeJobId = null;
  }

  function currentPollDelay() {
    var base = document.hidden ? POLL_MS_BACKGROUND : POLL_MS_FOREGROUND;
    if (pollFailStreak <= 0) return base;
    // Exponential backoff after failed updates (cap ~30s).
    return Math.min(30000, base * Math.pow(1.6, Math.min(pollFailStreak, 6)));
  }

  function setMailingActiveUi(on) {
    try {
      document.body.classList.toggle('mailing-active', !!on);
    } catch (_e) {}
  }

  function hideJobBanner() {
    if (jobBanner) jobBanner.hidden = true;
  }

  function showJobBanner(data) {
    if (!jobBanner || !jobBannerText) return;
    if (progressRoot && !progressRoot.hidden) {
      hideJobBanner();
      return;
    }
    var percent = data && data.percent != null ? data.percent : 0;
    var processed = data && data.processed != null ? data.processed : 0;
    var total = data && data.total != null ? data.total : 0;
    jobBannerText.textContent =
      'Розсилка триває на сервері: ' + processed + '/' + total + ' (' + percent + '%)';
    jobBanner.hidden = false;
    setMailingActiveUi(true);
  }

  function showProgressPanel() {
    if (progressRoot) progressRoot.hidden = false;
    hideJobBanner();
    setMailingActiveUi(true);
  }

  function parkEditorForMailing() {
    lastProgressKey = '';
    if (visualEditMode) {
      if (enableVisualEdit) enableVisualEdit.checked = false;
      visualEditMode = false;
      try { disableVisualEditing(); } catch (_e) {}
    }
    clearHeavyPreview('Превʼю приховано на час розсилки');
    // Drop large textarea buffer from layout while mailing (value stays in JS).
    if (body && body.value) {
      if (!canonicalMailHtml) rememberMailHtml(body.value);
      body.setAttribute('data-parked', '1');
      body.value = '';
    }
  }

  function restoreEditorAfterMailing() {
    if (body && body.getAttribute('data-parked') === '1') {
      body.removeAttribute('data-parked');
      if (canonicalMailHtml && !body.value) body.value = canonicalMailHtml;
    }
  }

  function showProgress(subject) {
    if (!progressRoot) return;
    showProgressPanel();
    // Tear down preview/visual-edit immediately; textarea is parked after body is captured for send.
    if (visualEditMode) {
      if (enableVisualEdit) enableVisualEdit.checked = false;
      visualEditMode = false;
      try { disableVisualEditing(); } catch (_e) {}
    }
    clearHeavyPreview('Превʼю приховано на час розсилки');
    progressRoot.classList.remove('is-done', 'is-error', 'is-cancelled');
    if (progressTitle) progressTitle.textContent = 'Розсилка триває…';
    if (progressSubject) progressSubject.textContent = subject ? ('Тема: ' + subject) : '';
    if (progressBar) progressBar.style.width = '0%';
    if (progressBarWrap) progressBarWrap.setAttribute('aria-valuenow', '0');
    if (progressStats) progressStats.textContent = 'Підготовка…';
    if (progressMessage) {
      progressMessage.hidden = true;
      progressMessage.textContent = '';
    }
    if (progressActions) progressActions.hidden = true;
    if (progressLiveActions) progressLiveActions.hidden = false;
    if (progressStop) {
      progressStop.disabled = false;
      progressStop.textContent = 'Зупинити розсилку';
    }
    stopInFlight = false;
    if (progressRoot) progressRoot.classList.remove('is-cancelled');
  }

  var lastProgressKey = '';

  function updateProgressUI(data) {
    if (!data) return;
    var percent = Number(data.percent || 0);
    var sent = Number(data.sent || 0);
    var failed = Number(data.failed || 0);
    var remaining = Number(data.remaining || 0);
    var total = Number(data.total || 0);
    var processed = Number(data.processed || (sent + failed));
    var status = String(data.status || '');
    var message = data.message ? String(data.message) : '';
    var key = [status, sent, failed, total, percent, message, data.cancel_requested ? '1' : '0'].join('|');
    if (key === lastProgressKey && !data.done) return;
    lastProgressKey = key;
    if (progressBar) progressBar.style.width = Math.max(0, Math.min(100, percent)) + '%';
    if (progressBarWrap) progressBarWrap.setAttribute('aria-valuenow', String(Math.round(percent)));
    if (progressStats) {
      progressStats.textContent =
        'Надіслано: ' + sent +
        ' · Помилок: ' + failed +
        ' · Залишилось: ' + remaining +
        ' · Усього: ' + total +
        ' (' + processed + '/' + total + ', ' + percent + '%)';
    }
    if (status === 'done') {
      progressRoot.classList.add('is-done');
      progressRoot.classList.remove('is-error', 'is-cancelled');
      if (progressTitle) progressTitle.textContent = 'Розсилку завершено';
    } else if (status === 'cancelled') {
      progressRoot.classList.add('is-cancelled');
      progressRoot.classList.remove('is-done', 'is-error');
      if (progressTitle) progressTitle.textContent = 'Розсилку зупинено';
    } else if (status === 'error') {
      progressRoot.classList.add('is-error');
      progressRoot.classList.remove('is-done', 'is-cancelled');
      if (progressTitle) progressTitle.textContent = 'Помилка розсилки';
    } else if (data.cancel_requested) {
      if (progressTitle) progressTitle.textContent = 'Зупиняємо розсилку…';
      if (progressStop) {
        progressStop.disabled = true;
        progressStop.textContent = 'Зупиняємо…';
      }
    }
    if (message && progressMessage) {
      progressMessage.hidden = false;
      progressMessage.textContent = message;
    }
    if (data.done && progressActions) {
      progressActions.hidden = false;
    }
    if (progressLiveActions) {
      progressLiveActions.hidden = !!data.done;
    }
  }

  function finishProgress(ok, status) {
    stopPolling();
    mailingInFlight = false;
    mailingFetchStarted = false;
    stopInFlight = false;
    setAllMailingSubmitButtonsDisabled(false);
    if (progressActions) progressActions.hidden = false;
    if (progressLiveActions) progressLiveActions.hidden = true;
    if (status === 'cancelled') {
      if (progressRoot) {
        progressRoot.classList.add('is-cancelled');
        progressRoot.classList.remove('is-done', 'is-error');
      }
      if (progressTitle) progressTitle.textContent = 'Розсилку зупинено';
    } else if (!ok && progressTitle) {
      progressTitle.textContent = 'Помилка розсилки';
    }
    hideJobBanner();
    setMailingActiveUi(false);
    restoreEditorAfterMailing();
    schedulePreviewRender();
  }

  function fetchJobStatus(jobId, opts) {
    opts = opts || {};
    if (pollAbort) {
      try { pollAbort.abort(); } catch (_e) {}
    }
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    pollAbort = controller;
    var syncQs = opts.sync ? '?sync=1' : '';
    var fetchOpts = {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store'
    };
    if (controller) fetchOpts.signal = controller.signal;
    return fetch('/clients/mailings/jobs/' + encodeURIComponent(jobId) + syncQs, fetchOpts)
      .then(function (r) {
        if (!r.ok) throw new Error('Не вдалося отримати статус розсилки');
        return r.json();
      })
      .finally(function () {
        if (pollAbort === controller) pollAbort = null;
      });
  }

  function handleJobStatus(data) {
    pollFailStreak = 0;
    if (progressRoot) progressRoot.classList.remove('is-error');
    updateProgressUI(data);
    if (data && data.message && progressMessage) {
      progressMessage.hidden = false;
      progressMessage.textContent = data.message;
    }
    if (data && !data.done && progressRoot && progressRoot.hidden) {
      showJobBanner(data);
    }
    if (data && data.done) {
      try { localStorage.removeItem('mailing_active_job_id'); } catch (e) {}
      hideJobBanner();
      showProgressPanel();
      finishProgress(data.status === 'done', data.status);
    }
  }

  function hideProgressKeepJob() {
    if (progressRoot) progressRoot.hidden = true;
    if (activeJobId && jobBannerText) {
      jobBannerText.textContent = 'Розсилка триває на сервері — натисніть, щоб відкрити прогрес';
      if (jobBanner) jobBanner.hidden = false;
      setMailingActiveUi(true);
    } else {
      setMailingActiveUi(false);
    }
  }

  function requestStopMailing() {
    if (!activeJobId || stopInFlight) return;
    var ok = true;
    try {
      ok = window.confirm(
        'Зупинити розсилку?\n\nУже надіслані листи залишаться доставленими. Нові адреси більше не отримають цей лист.'
      );
    } catch (_e) {}
    if (!ok) return;
    stopInFlight = true;
    if (progressStop) {
      progressStop.disabled = true;
      progressStop.textContent = 'Зупиняємо…';
    }
    if (progressTitle) progressTitle.textContent = 'Зупиняємо розсилку…';
    fetch('/clients/mailings/jobs/' + encodeURIComponent(activeJobId) + '/cancel', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
        'X-Requested-With': 'fetch',
        'X-CSRF-Token': getCsrfToken()
      }
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error((data && data.message) || (data && data.detail) || 'Не вдалося зупинити');
          return data;
        });
      })
      .then(function (data) {
        if (data && data.done) {
          updateProgressUI(data);
          try { localStorage.removeItem('mailing_active_job_id'); } catch (_e2) {}
          finishProgress(false, data.status || 'cancelled');
          return;
        }
        updateProgressUI(data || {});
        // Keep polling until sender loop finalizes cancel.
        if (activeJobId) scheduleNextPoll(800);
      })
      .catch(function (err) {
        stopInFlight = false;
        if (progressStop) {
          progressStop.disabled = false;
          progressStop.textContent = 'Зупинити розсилку';
        }
        if (progressMessage) {
          progressMessage.hidden = false;
          progressMessage.textContent =
            (err && err.message) ? err.message : 'Не вдалося надіслати команду зупинки';
        }
      });
  }

  function scheduleNextPoll(delayMs) {
    if (!activeJobId) return;
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(function () {
      if (!activeJobId) return;
      var jobId = activeJobId;
      // After several failed light polls, one sync may recover stuck counters.
      var wantSync = pollFailStreak >= 2 && pollFailStreak % 3 === 2;
      fetchJobStatus(jobId, { sync: wantSync })
        .then(handleJobStatus)
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          pollFailStreak += 1;
          if (progressRoot) progressRoot.classList.remove('is-error');
          if (progressMessage) {
            progressMessage.hidden = false;
            progressMessage.textContent =
              'Тимчасово не вдалося оновити прогрес (' + pollFailStreak + '): ' +
              ((err && err.message) ? err.message : 'Load failed') +
              '. Розсилка на сервері продовжується — можна закрити вкладку і повернутися пізніше.';
          }
          if (progressActions) progressActions.hidden = false;
          if (jobBannerText) {
            jobBannerText.textContent =
              'Розсилка триває на сервері (оновлення прогресу тимчасово недоступне)';
            if (jobBanner) jobBanner.hidden = false;
          }
        })
        .finally(function () {
          if (activeJobId) scheduleNextPoll(currentPollDelay());
        });
    }, delayMs == null ? currentPollDelay() : delayMs);
  }

  function pollJob(jobId) {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    activeJobId = jobId;
    pollFailStreak = 0;
    try {
      localStorage.setItem('mailing_active_job_id', jobId);
    } catch (e) {}
    scheduleNextPoll(0);
  }

  function attachToActiveJob(jobId, subject) {
    if (!jobId) return;
    mailingInFlight = true;
    mailingFetchStarted = true;
    setAllMailingSubmitButtonsDisabled(true);
    showProgress(subject || '');
    parkEditorForMailing();
    if (progressMessage) {
      progressMessage.hidden = false;
      progressMessage.textContent =
        'Розсилка вже йде на сервері. Можна закрити вкладку — відправка не зупиниться.';
    }
    // Single poll path (no parallel fetch) — safer on iPad Safari.
    pollJob(jobId);
  }

  function recoverActiveMailingJob() {
    var savedJobId = '';
    try {
      savedJobId = localStorage.getItem('mailing_active_job_id') || '';
    } catch (_e0) {}

    function fallbackListActive() {
      fetch('/clients/mailings/jobs?active=1', {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store'
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) {
          var jobs = payload && payload.jobs ? payload.jobs : [];
          if (jobs.length) {
            attachToActiveJob(jobs[0].job_id, jobs[0].subject || '');
          }
        })
        .catch(function () {});
    }

    // Prefer known job id → one light GET (skip globbing all job files).
    if (savedJobId) {
      fetchJobStatus(savedJobId)
        .then(function (data) {
          if (!data) {
            fallbackListActive();
            return;
          }
          if (data.done) {
            try { localStorage.removeItem('mailing_active_job_id'); } catch (_e1) {}
            // Do not auto-attach another unrelated active job after ours finished.
            return;
          }
          attachToActiveJob(savedJobId, data.subject || '');
          updateProgressUI(data);
        })
        .catch(function () {
          // Saved id missing/unreachable — only then look for any active job.
          fallbackListActive();
        });
      return;
    }
    // No local job id: do not surprise-attach someone else's leftover mailing.
    // User can still see progress if they started from this browser (localStorage set).
  }

  function stageMailingBody(html, csrf) {
    return fetch('/clients/mailings/stage-body', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'text/html; charset=utf-8',
        'X-Requested-With': 'fetch',
        'X-CSRF-Token': csrf
      },
      body: html
    }).then(function (r) {
      if (r.status === 413) {
        throw new Error('Лист занадто великий для сервера (413 на /stage-body).');
      }
      return r.json().then(function (data) {
        if (!r.ok || !data || !data.ok || !data.body_key) {
          throw new Error((data && data.error) ? data.error : 'Не вдалося зберегти HTML на сервері');
        }
        return data;
      });
    });
  }

  function buildMailingStartPayload(bodyKey) {
    var isHtmlEl = document.getElementById('isHtml');
    var saveTplEl = document.getElementById('saveAsTemplate');
    var tplNameEl = document.getElementById('mailTemplateName');
    var fileTplEl = document.getElementById('fileTemplateSelect');
    var targetEl = document.getElementById('targetMode');
    var customEl = document.getElementById('customRecipients');
    var mode = String((targetEl && targetEl.value) || 'all');
    var customText = mode === 'custom' ? String((customEl && customEl.value) || '') : '';
    var est = 0;
    if (mode === 'all') {
      est = eligibleRecipientsFromServer;
    } else if (stagedRecipientsCount && !customText.trim()) {
      est = stagedRecipientsCount;
    }
    return {
      subject: String((subjectInput && subjectInput.value) || '').trim(),
      body: '',
      body_key: bodyKey,
      is_html: !!(isHtmlEl && isHtmlEl.checked),
      target_mode: mode,
      custom_recipients: customText,
      recipients_key: mode === 'custom' ? stagedRecipientsKey : '',
      estimated_recipients: est,
      save_as_template: !!(saveTplEl && saveTplEl.checked),
      template_name: String((tplNameEl && tplNameEl.value) || ''),
      file_template_name: String((fileTplEl && fileTplEl.value) || '')
    };
  }

  function startAsyncMailing(form, opts) {
    opts = opts || {};
    if (mailingFetchStarted) return;
    mailingFetchStarted = true;
    if (!mailingInFlight) {
      mailingInFlight = true;
      setAllMailingSubmitButtonsDisabled(true);
    }
    var subject = opts.subject || '';
    if (visualEditMode) syncPreviewToTextarea();
    syncBodyFieldForSubmit();
    var bodyVal = bodyHidden ? String(bodyHidden.value || '') : getMailHtmlForSubmit();
    rememberMailHtml(bodyVal);
    showProgress(subject);
    parkEditorForMailing();
    var sizeKb = Math.round((bodyVal.length || 0) / 1024);
    var hasRecipientsFile = !!(recipientsFileInput && recipientsFileInput.files && recipientsFileInput.files.length);
    var csrf = getCsrfToken();
    var fetchUrl = '/clients/mailings/send-json';
    var fetchOpts = {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
        'X-Requested-With': 'fetch',
        'X-CSRF-Token': csrf
      }
    };

    stageMailingBody(bodyVal, csrf)
      .then(function (staged) {
        var payload = buildMailingStartPayload(staged.body_key);
        if (hasRecipientsFile) {
          fetchUrl = '/clients/mailings/send-json-with-file';
          var fd = new FormData();
          fd.append('payload', JSON.stringify(payload));
          fd.append('recipients_file', recipientsFileInput.files[0]);
          fetchOpts.body = fd;
        } else {
          fetchUrl = '/clients/mailings/send-json';
          fetchOpts.headers['Content-Type'] = 'application/json';
          fetchOpts.body = JSON.stringify(payload);
        }
        return fetch(fetchUrl, fetchOpts);
      })
      .then(function (r) {
        if (r.status === 413) {
          var fileHint = hasRecipientsFile
            ? ' Обрано файл отримувачів — спробуйте прибрати його («Прибрати файл») або зменшити CSV/XLSX.'
            : '';
          throw new Error(
            'Запит відхилено (413). Розмір HTML ~' + sizeKb + ' KB, URL: ' + fetchUrl + '.' + fileHint +
            ' Якщо лист маленький — перевірте проксі (client_max_body_size) і оновіть сторінку Ctrl+F5.'
          );
        }
        if (r.status === 400) {
          return r.json().then(function (data) {
            var detail = (data && (data.error || data.detail)) ? (data.error || data.detail) : '';
            if (String(detail).indexOf('maximum size') !== -1) {
              throw new Error(
                'Поле форми перевищило ліміт завантаження (Starlette multipart). Оновіть сторінку (Ctrl+F5) — ' +
                'звичайна розсилка без файлу тепер використовує /send-json.'
              );
            }
            return { ok: r.ok, status: r.status, data: data };
          });
        }
        return r.json().then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        }).catch(function () {
          throw new Error('Сервер повернув неочікувану відповідь (' + r.status + ')');
        });
      })
      .then(function (res) {
        if (res.status === 409 && res.data && res.data.job_id) {
          if (progressMessage) {
            progressMessage.hidden = false;
            progressMessage.textContent =
              res.data.error || 'Розсилка вже виконується — показуємо прогрес існуючого завдання.';
          }
          pollJob(res.data.job_id);
          return;
        }
        if (!res.ok || !res.data || !res.data.ok || !res.data.job_id) {
          var errMsg = (res.data && res.data.error) ? res.data.error : 'Не вдалося запустити розсилку';
          throw new Error(errMsg);
        }
        if (progressSubject && res.data.subject) {
          progressSubject.textContent = 'Тема: ' + res.data.subject;
        }
        updateProgressUI({
          status: 'running',
          total: res.data.total || 0,
          sent: 0,
          failed: 0,
          remaining: res.data.total || 0,
          processed: 0,
          percent: 0
        });
        pollJob(res.data.job_id);
      })
      .catch(function (err) {
        if (progressRoot) progressRoot.classList.add('is-error');
        if (progressTitle) progressTitle.textContent = 'Помилка розсилки';
        if (progressMessage) {
          progressMessage.hidden = false;
          progressMessage.textContent = (err && err.message) ? err.message : 'Помилка запуску';
        }
        finishProgress(false);
      });
  }

  if (progressReload) {
    progressReload.addEventListener('click', function () {
      window.location.reload();
    });
  }
  if (progressClose) {
    progressClose.addEventListener('click', function () {
      hideProgressKeepJob();
      if (!activeJobId) {
        if (progressRoot) progressRoot.hidden = true;
        setMailingActiveUi(false);
      }
    });
  }
  if (progressHide) {
    progressHide.addEventListener('click', hideProgressKeepJob);
  }
  if (progressStop) {
    progressStop.addEventListener('click', requestStopMailing);
  }
  if (jobBannerShow) {
    jobBannerShow.addEventListener('click', function () {
      showProgressPanel();
      hideJobBanner();
    });
  }
  if (jobBanner) {
    jobBanner.addEventListener('click', function (e) {
      if (e.target === jobBannerShow) return;
      showProgressPanel();
      hideJobBanner();
    });
  }

  document.addEventListener('visibilitychange', function () {
    if (!activeJobId) return;
    if (document.hidden) {
      if (pollAbort) {
        try { pollAbort.abort(); } catch (_e) {}
        pollAbort = null;
      }
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
      return;
    }
    scheduleNextPoll(0);
  });

  window.addEventListener('pagehide', function () {
    if (pollAbort) {
      try { pollAbort.abort(); } catch (_e) {}
      pollAbort = null;
    }
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  });

  window.addEventListener('pageshow', function () {
    if (activeJobId) scheduleNextPoll(0);
  });

  function applyCampaignToForm(c) {
    if (!c || !body || !subjectInput) return;
    if (visualEditMode) {
      if (enableVisualEdit) enableVisualEdit.checked = false;
      visualEditMode = false;
      disableVisualEditing();
    }
    subjectInput.value = c.subject || '';
    body.value = c.body || '';
    rememberMailHtml(c.body || '');
    if (isHtml) isHtml.checked = !!c.is_html;
    if (targetMode && c.target_mode) {
      targetMode.value = c.target_mode === 'custom' ? 'custom' : 'all';
      toggleCustomRecipients();
    }
    if (fileTemplateSelect) fileTemplateSelect.value = '';
    loadedHtmlFileName = '';
    updateTemplateHint();
    updateSubjectCount();
    renderPreview();
    if (mailingsForm) {
      mailingsForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function fetchCampaignDetail(campaignId) {
    return fetch('/clients/mailings/campaigns/' + encodeURIComponent(campaignId), {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    }).then(function (r) {
      if (!r.ok) throw new Error('Не вдалося завантажити кампанію');
      return r.json();
    }).then(function (data) {
      if (!data || !data.ok) throw new Error('Кампанію не знайдено');
      return data;
    });
  }

  function useCampaignAgain(campaignId) {
    var c = campaignsById[String(campaignId)];
    if (!c || !body || !subjectInput) return;
    if (c._bodyLoaded) {
      applyCampaignToForm(c);
      return;
    }
    var btn = document.querySelector('.js-use-campaign[data-campaign-id="' + String(campaignId) + '"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Завантаження…';
    }
    fetchCampaignDetail(campaignId)
      .then(function (data) {
        c.body = data.body || '';
        c.is_html = !!data.is_html;
        c.target_mode = data.target_mode || c.target_mode;
        c.subject = data.subject || c.subject;
        c._bodyLoaded = true;
        applyCampaignToForm(c);
      })
      .catch(function (err) {
        showMailNotice((err && err.message) || 'Не вдалося завантажити лист', 'Помилка');
      })
      .finally(function () {
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Використати';
        }
      });
  }

  function fillCampaignBodyNode(node, data) {
    if (!node) return;
    var isHtmlBody = !!(data && data.is_html);
    node.innerHTML = '';
    if (isHtmlBody) {
      var wrap = document.createElement('div');
      wrap.className = 'mail-snippet-html';
      wrap.innerHTML = data.body || '';
      node.appendChild(wrap);
    } else {
      var p = document.createElement('p');
      p.className = 'mail-snippet';
      p.textContent = (data && data.body) || '';
      node.appendChild(p);
    }
    node.setAttribute('data-loaded', '1');
  }

  function initFlashMessages() {
    document.querySelectorAll('.mail-flash-wrap').forEach(function (wrap) {
      var flash = wrap.querySelector('.mail-flash');
      if (!flash) return;
      wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      var closeBtn = wrap.querySelector('.mail-flash-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function () {
          wrap.remove();
        });
      }
    });
  }

  function initCampaignList() {
    document.querySelectorAll('.js-view-campaign').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-target');
        var node = id ? document.getElementById(id) : null;
        if (!node) return;
        var opening = !node.classList.contains('open');
        node.classList.toggle('open', opening);
        btn.textContent = opening ? 'Сховати' : 'Переглянути';
        if (!opening || node.getAttribute('data-loaded') === '1') return;
        var campaignId = node.getAttribute('data-campaign-id');
        if (!campaignId) return;
        var placeholder = node.querySelector('.mail-list-body-placeholder');
        if (placeholder) placeholder.textContent = 'Завантаження…';
        btn.disabled = true;
        fetchCampaignDetail(campaignId)
          .then(function (data) {
            var cached = campaignsById[String(campaignId)];
            if (cached) {
              cached.body = data.body || '';
              cached.is_html = !!data.is_html;
              cached._bodyLoaded = true;
            }
            fillCampaignBodyNode(node, data);
          })
          .catch(function () {
            if (placeholder) {
              placeholder.textContent = 'Не вдалося завантажити лист. Спробуйте ще раз.';
            }
            node.classList.remove('open');
            btn.textContent = 'Переглянути';
          })
          .finally(function () {
            btn.disabled = false;
          });
      });
    });

    document.querySelectorAll('.js-use-campaign').forEach(function (btn) {
      btn.addEventListener('click', function () {
        useCampaignAgain(btn.getAttribute('data-campaign-id'));
      });
    });

    document.querySelectorAll('.js-resend-form').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (mailingInFlight || mailingFetchStarted) return;
        var subject = form.getAttribute('data-subject') || 'розсилку';
        var resendBtn = form.querySelector('button[type="submit"]');
        setSubmitButtonBusy(resendBtn, true);
        openMailConfirm({
          title: 'Повторно надіслати?',
          lead: 'Після підтвердження розсилка запуститься одразу.',
          meta: [{ label: 'Тема', value: subject }],
          triggerEl: resendBtn,
          confirmLabel: 'Так, розіслати знову',
          fallbackText: 'Повторно надіслати «' + subject + '»?',
          onCancel: function () {},
          onConfirm: function () {
            if (mailingFetchStarted) return;
            mailingFetchStarted = true;
            mailingInFlight = true;
            setAllMailingSubmitButtonsDisabled(true);
            startAsyncMailing(form, { subject: subject });
          }
        });
      });
    });
  }

  if (preview) {
    preview.addEventListener('click', function (e) {
      if (!visualEditMode) return;
      var clicked = e.target;
      if (clicked.tagName === 'IMG') {
        e.preventDefault();
        setSelectedElement(clicked);
        return;
      }
      var relImg = findImgInRelativeContainer(clicked);
      if (relImg) {
        e.preventDefault();
        setSelectedElement(relImg);
        return;
      }
      var bgNode = findBackgroundEditableFrom(clicked);
      if (bgNode) {
        e.preventDefault();
        setSelectedElement(bgNode);
        openBackgroundQuickEdit(bgNode);
        return;
      }
      var target = clicked.closest('.ve-editable');
      if (!target || !preview.contains(target)) return;
      setSelectedElement(target);
    });

    preview.addEventListener('input', function () {
      if (!visualEditMode) return;
      syncPreviewToTextarea();
    });

    preview.addEventListener('dragover', function (e) {
      if (!visualEditMode) return;
      var hasFiles = e.dataTransfer && e.dataTransfer.types && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') !== -1;
      if (!hasFiles) return;
      e.preventDefault();
      if (visualDropHint) visualDropHint.classList.add('dragging');
      var hoverImg = e.target && e.target.closest ? e.target.closest('img') : null;
      preview.querySelectorAll('.ve-drop-target').forEach(function (n) { n.classList.remove('ve-drop-target'); });
      if (hoverImg && preview.contains(hoverImg)) hoverImg.classList.add('ve-drop-target');
    });

    preview.addEventListener('dragleave', function () {
      if (!visualEditMode) return;
      if (visualDropHint) visualDropHint.classList.remove('dragging');
      preview.querySelectorAll('.ve-drop-target').forEach(function (n) { n.classList.remove('ve-drop-target'); });
    });

    preview.addEventListener('drop', function (e) {
      if (!visualEditMode) return;
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      e.preventDefault();
      if (visualDropHint) visualDropHint.classList.remove('dragging');
      preview.querySelectorAll('.ve-drop-target').forEach(function (n) { n.classList.remove('ve-drop-target'); });

      var droppedFile = files[0];
      var dropImg = e.target && e.target.closest ? e.target.closest('img') : null;
      if (dropImg && preview.contains(dropImg)) {
        setSelectedElement(dropImg);
      } else if (!selectedImageTarget()) {
        alert('Перетягніть файл на конкретне зображення у превʼю або спочатку оберіть блок із фото.');
        return;
      }

      uploadImageForSelectedElement(droppedFile)
        .then(function () { alert('Фото завантажено на сайт і застосовано.'); })
        .catch(function (err) {
          if (err && err.message && err.message !== 'visual edit off' && err.message !== 'no image target' && err.message !== 'no file') {
            alert('Помилка завантаження фото: ' + err.message);
          }
        });
    });
  }

  if (body) {
    body.addEventListener('input', function () {
      rememberMailHtml(body.value);
      schedulePreviewRender();
    });
  }
  if (isHtml) isHtml.addEventListener('change', renderPreview);
  if (targetMode) targetMode.addEventListener('change', toggleCustomRecipients);
  if (fileTemplateSelect) fileTemplateSelect.addEventListener('change', applyFileTemplate);
  if (recipientsFileInput) recipientsFileInput.addEventListener('change', showSelectedFileName);
  if (recipientsFileClear) recipientsFileClear.addEventListener('click', clearRecipientsFile);
  if (saveNewTemplateBtn) saveNewTemplateBtn.addEventListener('click', saveNewTemplate);
  if (applyVisualLinkBtn) applyVisualLinkBtn.addEventListener('click', applyLinkToSelectedElement);
  if (removeVisualLinkBtn) removeVisualLinkBtn.addEventListener('click', removeLinkFromSelectedElement);
  if (mailingsForm) mailingsForm.addEventListener('submit', confirmBeforeSending);
  initMailDialogs();
  if (customRecipientsInput) customRecipientsInput.addEventListener('input', updateRecipientCountBadge);
  if (subjectInput) subjectInput.addEventListener('input', updateSubjectCount);

  if (enableVisualEdit) {
    enableVisualEdit.addEventListener('change', function () {
      visualEditMode = enableVisualEdit.checked;
      if (visualEditMode) {
        enableVisualEditing();
      } else {
        disableVisualEditing();
      }
      renderPreview();
    });
  }

  if (recipientsDropzone && recipientsFileInput) {
    ['dragenter', 'dragover'].forEach(function (eventName) {
      recipientsDropzone.addEventListener(eventName, function (e) {
        e.preventDefault();
        e.stopPropagation();
        recipientsDropzone.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (eventName) {
      recipientsDropzone.addEventListener(eventName, function (e) {
        e.preventDefault();
        e.stopPropagation();
        recipientsDropzone.classList.remove('dragover');
      });
    });
    recipientsDropzone.addEventListener('drop', function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      recipientsFileInput.files = files;
      showSelectedFileName();
    });
  }

  if (mailActions) mailActions.classList.add('is-sticky');

  initMailBodyDrop();
  toggleCustomRecipients();
  showSelectedFileName();
  updateTemplateHint();
  updateSubjectCount();
  renderPreview();
  initFlashMessages();
  initCampaignList();

  // Let the first paint finish before attaching to a running job (critical on iPad).
  if (isTouchUi) {
    setTimeout(recoverActiveMailingJob, 350);
  } else {
    recoverActiveMailingJob();
  }
})();
