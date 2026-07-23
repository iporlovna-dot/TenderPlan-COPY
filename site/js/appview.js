/* Рендер и интерактивность app.html — площадка поиска торгов.
   Точка входа = поиск по ключевым словам. Лента = карточки закупок.
   Сверка по ТЗ (score/checks) — надстройка, включается тумблером. */

(function () {

  const params = new URLSearchParams(window.location.search);

  // ---------- гейт входа ----------

  if (!LK.isLoggedIn()) {
    if (params.get("demo") === "1") {
      LK.seedDemo();
    } else {
      window.location.href = "login.html";
      return;
    }
  }

  const company = LK.getCompany();

  const DEFAULT_FILTERS = () => ({
    customer: "", region: "all", delivery: "all",
    law: "all", stage: "active",
    priceMin: 0, priceMax: null, source: "all", windowDays: 999
  });

  const state = {
    query: "",
    minus: "",
    searchId: null,          // id сохранённого поиска или null (свободный / «Все закупки»)
    filters: DEFAULT_FILTERS(),
    sort: "fresh",
    matchEnabled: false,
    matchProductId: null
  };

  // ---------- шапка / юзер ----------

  document.getElementById("user-company").textContent = company.name;
  document.getElementById("user-avatar").textContent =
    company.name.replace(/[^А-ЯA-Z]/g, "").slice(0, 2) || "ЛК";
  document.getElementById("plan-name").textContent =
    company.plan === "business" ? "Тариф «Бизнес»" :
    company.plan === "corp" ? "Тариф «Корпоративный»" : "Тариф «Старт»";

  document.getElementById("logout-btn").addEventListener("click", () => {
    LK.clearSession();
    window.location.href = "index.html";
  });

  // ---------- сайдбар: сохранённые поиски ----------

  function renderSidebar() {
    const searches = LK.getSearches();
    const wrap = document.getElementById("saved-searches");
    if (!searches.length) {
      wrap.innerHTML = `<div class="saved-empty">Пока нет сохранённых поисков.<br>Задайте ключевые слова и нажмите «Сохранить поиск».</div>`;
    } else {
      wrap.innerHTML = searches.map(s => `
        <a href="#" class="saved-search ${s.id === state.searchId ? "is-active" : ""}" data-search="${s.id}">
          <span class="saved-search__name">${lkEscape(s.name)}</span>
          ${s.newCount ? `<span class="saved-search__count">${s.newCount}</span>` : ""}
          <button class="saved-search__edit" data-edit="${s.id}" title="Изменить поиск" aria-label="Изменить">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L18.5 9.5a2.12 2.12 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </a>`).join("");
    }

    wrap.querySelectorAll("[data-search]").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-edit]")) return;
        e.preventDefault();
        loadSearch(el.dataset.search);
      });
    });
    wrap.querySelectorAll("[data-edit]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openSearchModal(btn.dataset.edit);
      });
    });
  }

  document.querySelector('[data-quick="all"]').addEventListener("click", (e) => {
    e.preventDefault();
    selectAllPurchases();
  });

  // ---------- загрузка поиска в состояние ----------

  function loadSearch(id) {
    const s = LK.getSearches().find(x => x.id === id);
    if (!s) return;
    state.searchId = id;
    state.query = s.query || "";
    state.minus = s.minus || "";
    const f = s.filters || {};
    state.filters = {
      customer: f.customer || "",
      region: f.region || "all",
      delivery: f.delivery || "all",
      law: f.law || "all",
      stage: f.stage || "active",
      priceMin: f.priceMin ?? 0,
      priceMax: f.priceMax ?? null,
      source: (f.sources && f.sources.length === 1) ? f.sources[0] : "all",
      windowDays: f.windowDays ?? 999
    };
    if (s.newCount) LK.updateSearch(id, { newCount: 0 });
    syncControlsFromState();
    renderSidebar();
    renderFeed();
  }

  function selectAllPurchases() {
    state.searchId = null;
    state.query = "";
    state.minus = "";
    state.filters = { ...DEFAULT_FILTERS(), stage: "all" };
    syncControlsFromState();
    renderSidebar();
    renderFeed();
  }

  function syncControlsFromState() {
    document.getElementById("search-input").value = state.query;
    document.getElementById("f-customer").value = state.filters.customer || "";
    document.getElementById("f-delivery").value = state.filters.delivery || "all";
    document.getElementById("f-law").value = state.filters.law;
    document.getElementById("f-stage").value = state.filters.stage;
    document.getElementById("f-region").value = state.filters.region;
    document.getElementById("f-price-min").value = state.filters.priceMin || 0;
    document.getElementById("f-price-max").value = state.filters.priceMax ?? "";
    document.getElementById("f-source").value = state.filters.source;
  }

  // регион и площадка — из реальных данных
  function populateFacets() {
    const all = LK.allPurchases();
    fillSelect("f-region", "all", "Вся РФ",
      [...new Set(all.map(p => p.region).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")));
    fillSelect("f-source", "all", "Все площадки",
      [...new Set(all.map(p => p.source).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")));
  }
  function fillSelect(id, allValue, allLabel, values) {
    const sel = document.getElementById(id);
    const cur = sel.value || allValue;
    sel.innerHTML = `<option value="${allValue}">${allLabel}</option>` +
      values.map(v => `<option value="${lkEscape(v)}">${lkEscape(v)}</option>`).join("");
    sel.value = [...sel.options].some(o => o.value === cur) ? cur : allValue;
  }

  // ---------- фильтры ----------

  document.getElementById("search-input").addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    state.searchId = null;          // свободный ввод «отвязывает» от шаблона
    renderSidebar();
    renderFeed();
  });
  document.getElementById("f-customer").addEventListener("input", (e) => { state.filters.customer = e.target.value.trim(); renderFeed(); });
  document.getElementById("f-delivery").addEventListener("change", (e) => { state.filters.delivery = e.target.value; renderFeed(); });
  document.getElementById("f-law").addEventListener("change", (e) => { state.filters.law = e.target.value; renderFeed(); });
  document.getElementById("f-stage").addEventListener("change", (e) => { state.filters.stage = e.target.value; renderFeed(); });
  document.getElementById("f-region").addEventListener("change", (e) => { state.filters.region = e.target.value; renderFeed(); });
  document.getElementById("f-source").addEventListener("change", (e) => { state.filters.source = e.target.value; renderFeed(); });
  document.getElementById("f-price-min").addEventListener("input", (e) => { state.filters.priceMin = Number(e.target.value) || 0; renderFeed(); });
  document.getElementById("f-price-max").addEventListener("input", (e) => {
    state.filters.priceMax = e.target.value === "" ? null : Number(e.target.value);
    renderFeed();
  });
  document.getElementById("filter-reset").addEventListener("click", () => {
    state.filters = { ...DEFAULT_FILTERS(), stage: state.filters.stage };
    syncControlsFromState();
    renderFeed();
  });
  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderFeed();
  });

  // ---------- надстройка: сверка по ТЗ ----------

  function renderMatchProducts() {
    const products = LK.getProducts();
    const sel = document.getElementById("match-product");
    const toggle = document.getElementById("match-enable");
    if (!products.length) {
      sel.innerHTML = `<option>нет карточек товара</option>`;
      sel.disabled = true;
      toggle.disabled = true;
      document.getElementById("match-hint").textContent = "— добавьте карточку товара, чтобы включить сверку по ТЗ";
      return;
    }
    sel.innerHTML = products.map(p => `<option value="${p.id}">${lkEscape(p.name)}</option>`).join("");
    state.matchProductId = state.matchProductId || products[0].id;
    sel.value = state.matchProductId;
  }

  document.getElementById("match-enable").addEventListener("change", (e) => {
    state.matchEnabled = e.target.checked;
    document.getElementById("match-product").disabled = !state.matchEnabled;
    document.getElementById("match-bar").classList.toggle("is-on", state.matchEnabled);
    document.getElementById("match-hint").style.display = state.matchEnabled ? "none" : "";
    renderFeed();
  });
  document.getElementById("match-product").addEventListener("change", (e) => {
    state.matchProductId = e.target.value;
    renderFeed();
  });

  function matchFor(p) {
    if (!state.matchEnabled || !state.matchProductId) return null;
    return (p.matches && p.matches[state.matchProductId]) || null;
  }

  // ---------- фильтрация и поиск ----------

  function tokens(str) {
    return (str || "").toLowerCase().split(/[\s,]+/).map(t => t.trim()).filter(Boolean);
  }
  // грубый стемминг: отсекаем окончание, чтобы «перчатки» ловило «перчаток».
  // Не лингвистика — временный приём для демо; на проде заменят морфоанализатор / эмбеддинги.
  function stem(word) {
    return word.length <= 4 ? word : word.slice(0, word.length - 2);
  }

  function passesSearch(p) {
    const hay = (p.title + " " + p.customer + " " + p.number + " " + p.okpd).toLowerCase();
    const plus = tokens(state.query);
    const minus = tokens(state.minus);
    if (minus.some(m => hay.includes(stem(m)))) return false;
    if (plus.length && !plus.some(w => hay.includes(stem(w)))) return false;
    return true;
  }

  function passesFilters(p) {
    const f = state.filters;
    if (f.customer) {
      const hay = (p.customer + " " + (p.customerInn || "")).toLowerCase();
      if (!hay.includes(f.customer.toLowerCase())) return false;
    }
    if (f.region !== "all" && p.region !== f.region) return false;
    if (f.delivery !== "all") {
      const max = Number(f.delivery);
      // неизвестный срок поставки не прячем
      if (p.deliveryDays != null && p.deliveryDays > max) return false;
    }
    if (f.law !== "all" && p.law !== f.law) return false;
    if (f.stage !== "all" && liveStage(p) !== f.stage) return false;
    if (f.source !== "all" && p.source !== f.source) return false;
    if (p.price < (f.priceMin || 0)) return false;
    if (f.priceMax != null && p.price > f.priceMax) return false;
    return true;
  }

  // ---------- рендер карточек ----------

  const STAGE = {
    active:    { label: "Подача идёт", cls: "stage-active" },
    committee: { label: "Работа комиссии", cls: "stage-committee" },
    completed: { label: "Завершена", cls: "stage-done" }
  };

  // живой отсчёт: срок считаем от текущего момента по endDate (а не по замороженному
  // deadlineDays из снапшота), чтобы просроченные сами отваливались из ленты.
  const DAY_MS = 86400000;
  function daysLeft(p) {
    if (p.endDate) return Math.ceil((new Date(p.endDate).getTime() - Date.now()) / DAY_MS);
    return p.deadlineDays ?? 0;
  }
  function isExpired(p) {
    return p.endDate ? new Date(p.endDate).getTime() <= Date.now() : false;
  }
  function liveStage(p) {
    if (p.endDate) return isExpired(p) ? "completed" : "active";
    return p.stage;
  }

  function highlight(text) {
    let out = lkEscape(text);
    const plus = tokens(state.query);
    plus.forEach(w => {
      const esc = stem(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      // подсвечиваем слово целиком, начиная от стема (перчато́к, нитрило́вых)
      out = out.replace(new RegExp("(" + esc + "[а-яёa-z0-9]*)", "gi"), "<mark>$1</mark>");
    });
    return out;
  }

  function verdictClass(v) { return v === "eligible" ? "ok" : v === "eligible_with_gaps" ? "gap" : "bad"; }
  function verdictBadge(v) {
    if (v === "eligible") return `<span class="badge badge-ok">Проходите</span>`;
    if (v === "eligible_with_gaps") return `<span class="badge badge-gap">Есть пробелы</span>`;
    return `<span class="badge badge-bad">Не подходит</span>`;
  }
  function checkClass(s) { return s === "pass" ? "ok" : s === "gap" ? "gap" : "bad"; }
  function checkIcon(s) { return s === "pass" ? "✓" : s === "gap" ? "⚠" : "✕"; }

  function deadlineText(p) {
    if (liveStage(p) === "active") {
      const d = Math.max(1, daysLeft(p));
      return `<span class="deadline"><b>${d}</b> ${lkPlural(d, ["день","дня","дней"])} до конца подачи</span>`;
    }
    return `<span class="deadline muted">${STAGE[liveStage(p)].label}</span>`;
  }

  function matchDetail(m) {
    const checks = m.checks.map(c => `
      <div class="check-row">
        <span>${lkEscape(c.req)}${c.note ? ` — <span class="check-note">${lkEscape(c.note)}</span>` : ""}</span>
        <span class="check-status ${checkClass(c.status)}">${checkIcon(c.status)}</span>
      </div>`).join("");
    return `
      <div class="detail-block detail-match">
        <div class="detail-block__title">Сверка с ТЗ ${verdictBadge(m.verdict)}</div>
        ${checks}
        <div class="explanation">${lkEscape(m.explanation)}</div>
      </div>`;
  }

  function purchaseDetail(p, m) {
    const lots = p.lots.map((l, i) => `
      <div class="lot-line">
        <span class="lot-line__idx">${p.lots.length > 1 ? (i + 1) + "." : "•"}</span>
        <span class="lot-line__name">${lkEscape(l.name)}</span>
        <span class="lot-line__qty">${lkEscape(l.qty)}</span>
        <span class="lot-line__price">${lkFormatMoney(l.price)}</span>
      </div>`).join("");

    const facts = [
      ["НМЦК", lkFormatMoney(p.price)],
      ["Обеспечение заявки", p.guaranteeApp ? lkFormatMoney(p.guaranteeApp) : "не требуется"],
      ["Обеспечение контракта", p.guaranteeContract ? lkFormatMoney(p.guaranteeContract) : "не требуется"],
      ["Аванс", p.prepayment ? p.prepayment + "%" : "нет"],
      ["Срок поставки", p.deliveryDays != null ? `${p.deliveryDays} ${lkPlural(p.deliveryDays, ["день","дня","дней"])}` : "—"],
      ["Опубликована", p.publishedDaysAgo === 0 ? "сегодня" : `${p.publishedDaysAgo} ${lkPlural(p.publishedDaysAgo, ["день","дня","дней"])} назад`],
      ["Место поставки", p.deliveryPlace || p.region || "—"]
    ].map(([k, v]) => `<div class="fact"><span class="fact__k">${k}</span><span class="fact__v">${lkEscape(String(v))}</span></div>`).join("");

    const docsHtml = (p.documents && p.documents.length) ? `
        <div class="detail-block">
          <div class="detail-block__title">Документы (${p.documents.length})</div>
          <div class="doc-list">
            ${p.documents.map(d => `<a class="doc-link" href="${lkEscape(d.url)}" target="_blank" rel="noopener noreferrer"><span class="doc-ic">📎</span><span class="doc-name">${lkEscape(d.name)}</span><span class="doc-dl">скачать ↓</span></a>`).join("")}
          </div>
        </div>` : "";

    return `
      <div class="tender-detail">
        ${m ? matchDetail(m) : ""}
        <div class="detail-block">
          <div class="detail-block__title">Позиции лота${p.lots.length > 1 ? ` (${p.lots.length})` : ""}</div>
          ${lots}
        </div>
        <div class="detail-block">
          <div class="detail-block__title">Условия закупки</div>
          <div class="facts-grid">${facts}</div>
        </div>
        ${docsHtml}
        <div class="tender-actions">
          ${p.href
            ? `<a class="btn btn-primary btn-sm" href="${lkEscape(p.href)}" target="_blank" rel="noopener noreferrer">Открыть на площадке ↗</a>`
            : `<span class="btn btn-primary btn-sm" style="opacity:.5;cursor:not-allowed;" title="Ссылка на площадку недоступна">Ссылка недоступна</span>`}
          <a class="btn btn-ghost btn-sm" href="#" onclick="return false;">Скрыть</a>
        </div>
      </div>`;
  }

  function cardHtml(p) {
    const m = matchFor(p);
    const fresh = p.publishedDaysAgo <= 2 ? `<span class="badge badge-fresh">новая</span>` : "";
    const lot = p.lotNote ? `<span class="lot-note">позиция ${p.lotNote.position} из ${p.lotNote.total}</span>` : "";
    const scoreCol = m
      ? `<div class="tender-score"><div class="tender-score__num ${verdictClass(m.verdict)}">${m.score}<span class="tender-score__pct">%</span></div><span class="tender-score__cap">совпадение ТЗ</span></div>`
      : "";

    return `
    <article class="tender-card ${m ? "has-match" : ""}" data-id="${p.id}">
      <div class="tender-card__main">
        ${scoreCol}
        <div class="tender-body">
          <div class="tender-body__top">
            <span class="badge badge-law ${p.law === "223-ФЗ" ? "is-223" : ""}">${p.law}</span>
            <span class="badge badge-stage ${STAGE[liveStage(p)].cls}">${STAGE[liveStage(p)].label}</span>
            <span class="badge badge-source">${lkEscape(p.source)}</span>
            ${fresh}${lot}
          </div>
          <h3 class="tender-title">${highlight(p.title)}</h3>
          <div class="tender-meta">
            <span><b>№${p.number}</b></span>
            <span>${lkEscape(p.customer)}</span>
            ${p.region ? `<span>${lkEscape(p.region)}</span>` : ""}
            ${p.okpd ? `<span>ОКПД2 ${lkEscape(p.okpd)}</span>` : ""}
          </div>
          <div class="tender-meta tender-meta--strong">
            <span>НМЦК: <b>${lkFormatMoney(p.price)}</b></span>
            ${deadlineText(p)}
          </div>
        </div>
        <svg class="tender-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      </div>
      ${purchaseDetail(p, m)}
    </article>`;
  }

  // ---------- лента ----------

  const PAGE = 60;
  let shownCount = PAGE;

  function renderFeed(resetPage = true) {
    if (resetPage) shownCount = PAGE;
    const feed = document.getElementById("feed-content");
    const title = document.getElementById("feed-title");
    const count = document.getElementById("feed-count");

    const current = state.searchId ? LK.getSearches().find(s => s.id === state.searchId) : null;
    title.textContent = current ? current.name : (state.query ? `Поиск: «${state.query}»` : "Все закупки");

    // просроченные (дедлайн подачи прошёл) убираем автоматически
    let list = LK.allPurchases().filter(p => !isExpired(p)).filter(passesSearch).filter(passesFilters);

    const sortFn = {
      fresh: (a, b) => a.publishedDaysAgo - b.publishedDaysAgo,
      deadline: (a, b) => rank(a) - rank(b) || daysLeft(a) - daysLeft(b),
      "price-desc": (a, b) => b.price - a.price,
      "price-asc": (a, b) => a.price - b.price,
      score: (a, b) => (score(b) - score(a)) || a.publishedDaysAgo - b.publishedDaysAgo
    }[state.sort];
    function rank(p) { return liveStage(p) === "active" ? 0 : 1; }
    function score(p) { const m = matchFor(p); return m ? m.score : -1; }

    list.sort(sortFn);

    if (!list.length) {
      count.textContent = "ничего не найдено";
      feed.innerHTML = `<div class="empty-state">
        <h3>Ничего не найдено</h3>
        <p>Под текущие ключевые слова и фильтры закупок нет. Попробуйте убрать минус-слова, расширить регион или сменить этап.</p>
      </div>`;
      return;
    }

    const shown = Math.min(shownCount, list.length);
    count.textContent =
      `${list.length} ${lkPlural(list.length, ["закупка","закупки","закупок"])}` +
      (shown < list.length ? ` · показаны ${shown}` : "");

    let html = list.slice(0, shown).map(cardHtml).join("");
    if (shown < list.length) {
      html += `<button class="btn btn-ghost btn-block load-more" id="load-more">
        Показать ещё ${Math.min(PAGE, list.length - shown)} из ${list.length - shown}</button>`;
    }
    feed.innerHTML = html;

    feed.querySelectorAll(".tender-card__main").forEach(el => {
      el.addEventListener("click", () => el.closest(".tender-card").classList.toggle("is-open"));
    });
    const more = document.getElementById("load-more");
    if (more) more.addEventListener("click", () => { shownCount += PAGE; renderFeed(false); });
  }

  // ---------- модалка поиска ----------

  const modal = document.getElementById("search-modal");
  let editingId = null;

  function openSearchModal(id) {
    editingId = id || null;
    const del = document.getElementById("search-delete");
    if (editingId) {
      const s = LK.getSearches().find(x => x.id === editingId);
      document.getElementById("search-modal-title").textContent = "Изменить поиск";
      document.getElementById("sf-name").value = s.name;
      document.getElementById("sf-query").value = s.query || "";
      document.getElementById("sf-minus").value = s.minus || "";
      document.getElementById("sf-law").value = (s.filters && s.filters.law) || "all";
      document.getElementById("sf-window").value = (s.filters && s.filters.windowDays) || 30;
      del.style.display = "";
    } else {
      document.getElementById("search-modal-title").textContent = "Новый поиск";
      document.getElementById("sf-name").value = "";
      document.getElementById("sf-query").value = state.query || "";
      document.getElementById("sf-minus").value = state.minus || "";
      document.getElementById("sf-law").value = "all";
      document.getElementById("sf-window").value = 30;
      del.style.display = "none";
    }
    modal.classList.add("is-open");
  }
  function closeSearchModal() { modal.classList.remove("is-open"); }

  document.getElementById("new-search-btn").addEventListener("click", () => openSearchModal(null));
  document.getElementById("search-cancel").addEventListener("click", closeSearchModal);
  document.getElementById("search-save-btn").addEventListener("click", () => openSearchModal(null));
  modal.addEventListener("click", (e) => { if (e.target === modal) closeSearchModal(); });

  document.getElementById("search-delete").addEventListener("click", () => {
    if (editingId) {
      LK.deleteSearch(editingId);
      if (state.searchId === editingId) selectAllPurchases();
      else renderSidebar();
      closeSearchModal();
      lkToast("Поиск удалён");
    }
  });

  document.getElementById("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = document.getElementById("sf-name").value.trim();
    if (!name) return;
    const payload = {
      name,
      query: document.getElementById("sf-query").value.trim(),
      minus: document.getElementById("sf-minus").value.trim(),
      filters: {
        law: document.getElementById("sf-law").value,
        stage: "active", region: "all", priceMin: 0, priceMax: null,
        windowDays: Number(document.getElementById("sf-window").value) || 30,
        sources: ["ЕИС", "РТС-тендер", "РТС-маркет", "Сбербанк-АСТ", "Росэлторг"]
      }
    };
    let id;
    if (editingId) {
      LK.updateSearch(editingId, payload);
      id = editingId;
      lkToast("Поиск обновлён");
    } else {
      const s = LK.addSearch({ ...payload, newCount: 0 });
      id = s.id;
      lkToast(`Поиск «${name}» сохранён`);
    }
    closeSearchModal();
    loadSearch(id);
  });

  // ---------- init ----------

  renderMatchProducts();
  renderSidebar();

  document.getElementById("feed-count").textContent = "загружаем закупки…";

  LK.loadPurchases().then(() => {
    populateFacets();  // регион/площадка из реальных данных
    // по умолчанию показываем реальные «Все закупки»; сохранённые поиски — в сайдбаре
    selectAllPurchases();
    // живое устаревание: раз в минуту перерисовываем (без сброса пагинации)
    setInterval(() => renderFeed(false), 60000);
  });

})();
