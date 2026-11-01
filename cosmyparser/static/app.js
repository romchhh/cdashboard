const form = document.getElementById("searchForm");
const queryInput = document.getElementById("query");
const priceInput = document.getElementById("ourPrice");
const searchBtn = document.getElementById("searchBtn");
const statusLine = document.getElementById("statusLine");
const summary = document.getElementById("summary");
const results = document.getElementById("results");
const historyList = document.getElementById("historyList");
const clearHistory = document.getElementById("clearHistory");

let activeHistoryId = null;

function money(value) {
  if (value == null || Number.isNaN(Number(value)) || Number(value) < 10) return "—";
  return `${Number(value).toLocaleString("uk-UA", { maximumFractionDigits: 0 })} ₴`;
}

function volumeChip(item) {
  const match = item.volume_match;
  const label = item.volume || "об’єм не вказано";
  if (match === "exact") return `<span class="chip ok">${label} · збіг</span>`;
  if (match === "mismatch") return `<span class="chip bad">${label} · інший об’єм</span>`;
  if (match === "option_no_price") return `<span class="chip warn">${label} · є варіант</span>`;
  return `<span class="chip">${label}</span>`;
}

function renderHistory(items, activeId) {
  if (!items || !items.length) {
    historyList.innerHTML = `<p class="muted">Поки порожньо. Після першого пошуку запити з’являться тут.</p>`;
    clearHistory.hidden = true;
    return;
  }
  clearHistory.hidden = false;
  historyList.innerHTML = items.map((item) => {
    const date = item.created_at ? new Date(item.created_at).toLocaleString("uk-UA") : "";
    const active = item.id === activeId ? " active" : "";
    return `
      <div class="history-item${active}" data-id="${item.id}">
        <div class="row">
          <strong>${escapeHtml(item.query)}</strong>
          <button class="icon-btn" data-delete="${item.id}" title="Видалити" type="button">×</button>
        </div>
        <span>${date}${item.found ? ` · знайдено ${item.found}` : ""}${item.market_avg ? ` · середня ${money(item.market_avg)}` : ""}</span>
      </div>
    `;
  }).join("");
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderSummary(data) {
  if (!data.results || !data.results.length) {
    summary.hidden = true;
    summary.innerHTML = "";
    return;
  }
  summary.hidden = false;
  summary.innerHTML = `
    <div class="stat"><span>Знайдено</span><b>${data.found} з ${data.sites_total || "—"}</b></div>
    <div class="stat"><span>Найдешевше</span><b>${money(data.market_min)}</b></div>
    <div class="stat"><span>Середня ціна</span><b>${money(data.market_avg)}</b></div>
    <div class="stat"><span>${data.our_price ? "Наша позиція" : "Найдорожче"}</span><b>${data.position || money(data.market_max)}</b></div>
  `;
}

function renderResults(data) {
  const cards = [];
  for (const item of data.results || []) {
    const photo = item.image
      ? `<img src="${escapeHtml(item.image)}" alt="${escapeHtml(item.title)}">`
      : `<div class="placeholder">Немає фото</div>`;
    const options = (item.options || []).filter((opt) => opt.volume || opt.packaging || opt.price);
    const optionRows = options.map((opt) => {
      const name = opt.label || [opt.volume, opt.packaging].filter(Boolean).join(" · ") || "варіант";
      const price = opt.price != null && Number(opt.price) >= 10 ? money(opt.price) : "ціна на сторінці";
      const href = opt.url ? ` href="${escapeHtml(opt.url)}" target="_blank" rel="noopener"` : "";
      const tag = opt.url ? "a" : "div";
      return `<${tag} class="variant-row"${href}><span>${escapeHtml(name)}</span><strong>${price}</strong></${tag}>`;
    }).join("");
    const notes = (item.spec_notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("");
    const extra = item.spf ? `<span class="chip">SPF ${item.spf}</span>` : "";
    const pack = item.packaging ? `<span class="chip">${escapeHtml(item.packaging)}</span>` : "";
    cards.push(`
      <article class="card">
        <div class="photo">${photo}</div>
        <div class="card-body">
          <div class="card-top">
            <span class="badge">${escapeHtml(item.site)}</span>
            <span class="match">${Math.round((item.match || 0) * 100)}% схожості</span>
          </div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="price">${money(item.price)}</div>
          <div class="chips">
            ${volumeChip(item)}
            ${pack}
            ${extra}
            ${item.price_note ? `<span class="chip warn">${escapeHtml(item.price_note)}</span>` : ""}
          </div>
          ${optionRows ? `<div class="variants"><p>Усі фасовки</p>${optionRows}</div>` : ""}
          ${notes ? `<ul class="notes">${notes}</ul>` : ""}
          <a class="card-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Відкрити товар</a>
        </div>
      </article>
    `);
  }
  for (const miss of data.misses || []) {
    cards.push(`
      <article class="card miss">
        <div class="photo"><div class="placeholder">${escapeHtml(miss.site)}</div></div>
        <div class="card-body">
          <div class="card-top"><span class="badge">${escapeHtml(miss.site)}</span></div>
          <h3>Не знайдено точний збіг</h3>
          <p class="muted">${escapeHtml(miss.reason || "")}${miss.best_title ? `: ${escapeHtml(miss.best_title)}` : ""}</p>
          ${miss.best_url ? `<a class="card-link" href="${escapeHtml(miss.best_url)}" target="_blank" rel="noopener">Дивитись найближчий</a>` : ""}
        </div>
      </article>
    `);
  }
  results.innerHTML = cards.join("") || `<p class="muted">Немає результатів.</p>`;
}

function renderPayload(data) {
  activeHistoryId = data.id || null;
  queryInput.value = data.query || queryInput.value;
  if (data.our_price != null) priceInput.value = data.our_price;
  renderSummary(data);
  renderResults(data);
  if (data.history) {
    renderHistory(data.history, data.id);
  } else {
    document.querySelectorAll(".history-item").forEach((node) => {
      node.classList.toggle("active", node.dataset.id === data.id);
    });
  }
  const found = data.found || 0;
  statusLine.textContent = found
    ? `Готово: ${found} магазини з точним або близьким товаром.`
    : "Жоден магазин не дав достатньо схожого товару. Спробуйте додати бренд або об’єм.";
}

async function loadHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  renderHistory(data.items || [], activeHistoryId);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;
  searchBtn.disabled = true;
  searchBtn.textContent = "Шукаю…";
  statusLine.textContent = "Обходимо сайти конкурентів, порівнюємо назву, об’єм і варіанти. Це може зайняти пів хвилини.";
  summary.hidden = true;
  results.innerHTML = "";
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        our_price: priceInput.value.trim(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      statusLine.textContent = data.error || "Помилка пошуку";
      return;
    }
    renderPayload(data);
  } catch (error) {
    statusLine.textContent = "Не вдалося виконати пошук. Перевірте, що сервер запущений.";
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = "Шукати";
  }
});

historyList.addEventListener("click", async (event) => {
  const del = event.target.closest("[data-delete]");
  if (del) {
    event.preventDefault();
    event.stopPropagation();
    const res = await fetch(`/api/history/${del.dataset.delete}`, { method: "DELETE" });
    const data = await res.json();
    renderHistory(data.items || [], activeHistoryId);
    return;
  }
  const item = event.target.closest(".history-item");
  if (!item) return;
  const res = await fetch(`/api/history/${item.dataset.id}`);
  if (!res.ok) return;
  const data = await res.json();
  renderPayload(data);
});

clearHistory.addEventListener("click", async () => {
  const res = await fetch("/api/history", { method: "DELETE" });
  const data = await res.json();
  renderHistory(data.items || [], null);
});

loadHistory();
pollReportStatus();

const reportBtn = document.getElementById("reportBtn");
const reportStatus = document.getElementById("reportStatus");
const reportDownload = document.getElementById("reportDownload");

function applyReportStatus(data) {
  if (data.has_file) reportDownload.hidden = false;
  if (data.state === "running") {
    reportBtn.disabled = true;
    reportBtn.textContent = "Збираю…";
    reportStatus.textContent = `Порівнюю ${data.current || 0} з ${data.total || 16}: ${data.product || "каталог"}`;
  } else if (data.state === "done") {
    reportBtn.disabled = false;
    reportBtn.textContent = "Оновити звіт";
    reportDownload.hidden = false;
    reportStatus.textContent = `Готово ${data.generated_at || ""}. Можна завантажити Excel.`;
  } else if (data.state === "error") {
    reportBtn.disabled = false;
    reportBtn.textContent = "Зібрати звіт";
    reportStatus.textContent = data.error || "Помилка звіту";
  }
}

async function pollReportStatus() {
  try {
    const res = await fetch("/api/report/status");
    const data = await res.json();
    applyReportStatus(data);
    if (data.state === "running") {
      setTimeout(pollReportStatus, 2500);
    }
  } catch (error) {
    /* ignore */
  }
}

reportBtn.addEventListener("click", async () => {
  reportBtn.disabled = true;
  reportBtn.textContent = "Збираю…";
  reportStatus.textContent = "Запускаю порівняння по 16 товарах. Це займе кілька хвилин.";
  try {
    const res = await fetch("/api/report/run", { method: "POST" });
    const data = await res.json();
    applyReportStatus(data);
    pollReportStatus();
  } catch (error) {
    reportBtn.disabled = false;
    reportStatus.textContent = "Не вдалося запустити звіт.";
  }
});
