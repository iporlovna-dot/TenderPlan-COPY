/* Рендер и интерактивность app.html: фильтры, лента, ондбординг товара. */

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

  const state = {
    query: "",
    sort: "score",
    region: "all",
    windowDays: 30,
    priceMin: 0,
    priceMax: 5000000,
    sources: new Set(["ЕИС", "РТС-тендер", "РТС-маркет", "Сбербанк-АСТ", "Росэлторг"]),
    lotMode: "any",
    disqOpen: false
  };

  // ---------- заголовок / юзер-меню ----------

  document.getElementById("user-company").textContent = company.name;
  document.getElementById("user-avatar").textContent = company.name.replace(/[^А-ЯA-Z]/g, "").slice(0, 2) || "ЛК";
  document.getElementById("plan-name").textContent =
    company.plan === "business" ? "Тариф «Бизнес»" : company.plan === "corp" ? "Тариф «Корпоративный»" : "Тариф «Старт»";

  document.getElementById("logout-btn").addEventListener("click", () => {
    LK.clearSession();
    window.location.href = "index.html";
  });

  // ---------- переключатель товара ----------

  function renderProductSwitch() {
    const products = LK.getProducts();
    const sel = document.getElementById("product-select");
    sel.innerHTML = products.map(p => `<option value="${p.id}">${lkEscape(p.name)}</option>`).join("");
    const current = LK.getCurrentProduct();
    if (current) sel.value = current.id;
  }

  document.getElementById("product-select").addEventListener("change", (e) => {
    LK.setCurrentProduct(e.target.value);
    syncFiltersFromProduct();
    renderFeed();
  });

  document.getElementById("add-product-link").addEventListener("click", (e) => {
    e.preventDefault();
    openOnboarding();
  });

  // ---------- фильтры ----------

  function syncFiltersFromProduct() {
    const p = LK.getCurrentProduct();
    if (!p || !p.search) return;
    state.windowDays = p.search.windowDays ?? 30;
    state.priceMin = p.search.priceMin ?? 0;
    state.priceMax = p.search.priceMax ?? 5000000;
    state.lotMode = p.search.lotMode ?? "any";
    state.sources = new Set(p.search.sources || ["ЕИС", "РТС-тендер", "РТС-маркет", "Сбербанк-АСТ", "Росэлторг"]);

    document.getElementById("f-window").value = state.windowDays;
    document.getElementById("f-price-min").value = state.priceMin;
    document.getElementById("f-price-max").value = state.priceMax;
    document.querySelectorAll("input[name=lot-mode]").forEach(r => r.checked = (r.value === state.lotMode));
    document.querySelectorAll(".f-source").forEach(cb => cb.checked = state.sources.has(cb.value));
  }

  document.getElementById("f-window").addEventListener("input", (e) => {
    state.windowDays = Number(e.target.value) || 0;
    renderFeed();
  });
  document.getElementById("f-price-min").addEventListener("input", (e) => {
    state.priceMin = Number(e.target.value) || 0;
    renderFeed();
  });
  document.getElementById("f-price-max").addEventListener("input", (e) => {
    state.priceMax = Number(e.target.value) || 999999999;
    renderFeed();
  });
  document.getElementById("f-region").addEventListener("change", (e) => {
    state.region = e.target.value;
    renderFeed();
  });
  document.querySelectorAll("input[name=lot-mode]").forEach(r => {
    r.addEventListener("change", (e) => { state.lotMode = e.target.value; renderFeed(); });
  });
  document.querySelectorAll(".f-source").forEach(cb => {
    cb.addEventListener("change", () => {
      state.sources = new Set([...document.querySelectorAll(".f-source:checked")].map(x => x.value));
      renderFeed();
    });
  });
  document.getElementById("search-input").addEventListener("input", (e) => {
    state.query = e.target.value.trim().toLowerCase();
    renderFeed();
  });
  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderFeed();
  });

  // ---------- лента ----------

  function verdictClass(v) { return v === "eligible" ? "ok" : v === "eligible_with_gaps" ? "gap" : "bad"; }
  function verdictBadge(v) {
    if (v === "eligible") return `<span class="badge badge-ok">Проходите</span>`;
    if (v === "eligible_with_gaps") return `<span class="badge badge-gap">Есть пробелы</span>`;
    return `<span class="badge badge-bad">Не подходит</span>`;
  }
  function checkClass(s) { return s === "pass" ? "ok" : s === "gap" ? "gap" : "bad"; }
  function checkIcon(s) { return s === "pass" ? "✓" : s === "gap" ? "⚠" : "✕"; }

  function cardHtml(t) {
    const lot = t.lot ? `<span class="lot-note">позиция ${t.lot.position} из ${t.lot.total}</span>` : "";
    const checksHtml = t.checks.map(c => `
      <div class="check-row">
        <span>${lkEscape(c.req)}${c.note ? ` — <span style="color:var(--ink-faint)">${lkEscape(c.note)}</span>` : ""}</span>
        <span class="check-status ${checkClass(c.status)}">${checkIcon(c.status)}</span>
      </div>`).join("");

    return `
    <article class="tender-card" data-id="${t.id}">
      <div class="tender-card__main">
        <div class="tender-score">
          <div class="tender-score__num ${verdictClass(t.verdict)}">${t.score}<span class="tender-score__pct">%</span></div>
        </div>
        <div class="tender-body">
          <div class="tender-body__top">${verdictBadge(t.verdict)}<span class="badge badge-source">${lkEscape(t.source)}</span>${lot}</div>
          <h3 class="tender-title">${lkEscape(t.title)}</h3>
          <div class="tender-meta">
            <span><b>№${t.number}</b></span>
            <span>${lkEscape(t.customer)}</span>
            <span>${lkEscape(t.region)}</span>
            <span>НМЦК: <b>${lkFormatMoney(t.price)}</b></span>
            <span>Подача: <b>${t.deadlineDays} дн.</b></span>
          </div>
        </div>
        <svg class="tender-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      </div>
      <div class="tender-detail">
        ${checksHtml}
        <div class="explanation">${lkEscape(t.explanation)}</div>
        <div class="tender-actions">
          <a class="btn btn-primary btn-sm" href="#" onclick="return false;">Открыть на площадке</a>
          <a class="btn btn-ghost btn-sm" href="#" onclick="return false;">Скрыть из ленты</a>
        </div>
      </div>
    </article>`;
  }

  function passesFilters(t) {
    if (state.region !== "all" && t.region !== state.region) return false;
    if (t.deadlineDays > state.windowDays) return false;
    if (t.price < state.priceMin || t.price > state.priceMax) return false;
    if (!state.sources.has(t.source)) return false;
    if (state.lotMode === "solo" && t.lot) return false;
    if (state.query) {
      const hay = (t.title + " " + t.customer + " " + t.number).toLowerCase();
      if (!hay.includes(state.query)) return false;
    }
    return true;
  }

  function renderFeed() {
    const product = LK.getCurrentProduct();
    const feedWrap = document.getElementById("feed-content");

    if (!product) {
      feedWrap.innerHTML = "";
      document.getElementById("empty-state").style.display = "block";
      document.getElementById("feed-header").style.display = "none";
      return;
    }
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("feed-header").style.display = "flex";

    const all = LK.allTenders().filter(t => t.productId === product.id).filter(passesFilters);

    const main = all.filter(t => t.verdict !== "disqualified");
    const disq = all.filter(t => t.verdict === "disqualified");

    const sortFn = {
      score: (a, b) => b.score - a.score || a.deadlineDays - b.deadlineDays,
      deadline: (a, b) => a.deadlineDays - b.deadlineDays,
      price: (a, b) => b.price - a.price
    }[state.sort];

    main.sort(sortFn);
    disq.sort(sortFn);

    document.getElementById("feed-count").textContent =
      all.length ? `${main.length} подходит · ${disq.length} не подходит` : "ничего не найдено под текущие фильтры";

    let html = "";
    if (!main.length && !disq.length) {
      html = `<div class="empty-state"><h3>По этому товару пока нет совпадений</h3>
        <p>Мы каждый день просматриваем новые закупки на всех подключённых площадках и пришлём уведомление,
        как только появится подходящая по вашей карточке и настройкам поиска.</p></div>`;
    } else {
      html += main.map(cardHtml).join("");
      if (disq.length) {
        html += `<div class="section-toggle" id="disq-toggle">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          Не подходит (${disq.length}) — дисквалифицированы по обязательным требованиям
        </div>
        <div id="disqualified-list">${disq.map(cardHtml).join("")}</div>`;
      }
    }
    feedWrap.innerHTML = html;

    feedWrap.querySelectorAll(".tender-card__main").forEach(el => {
      el.addEventListener("click", () => el.closest(".tender-card").classList.toggle("is-open"));
    });
    const toggle = document.getElementById("disq-toggle");
    if (toggle) {
      toggle.addEventListener("click", () => {
        toggle.classList.toggle("is-open");
        document.getElementById("disqualified-list").classList.toggle("is-open");
      });
    }
  }

  // ---------- ондбординг: добавить товар ----------

  const modal = document.getElementById("onboarding-modal");
  let attrCount = 0;

  function attrRow(key = "", value = "", critical = false) {
    attrCount++;
    const id = "attr-" + attrCount;
    return `<div class="attr-row" id="${id}">
      <input type="text" placeholder="характеристика (напр. материал)" class="attr-key" value="${lkEscape(key)}">
      <input type="text" placeholder="значение (напр. нитрил)" class="attr-value" value="${lkEscape(value)}">
      <button type="button" class="attr-remove" onclick="document.getElementById('${id}').remove()">✕</button>
    </div>`;
  }

  function openOnboarding() {
    document.getElementById("attrs-wrap").innerHTML = attrRow() + attrRow() + attrRow();
    modal.classList.add("is-open");
  }
  function closeOnboarding() { modal.classList.remove("is-open"); }

  document.getElementById("add-attr-btn").addEventListener("click", () => {
    document.getElementById("attrs-wrap").insertAdjacentHTML("beforeend", attrRow());
  });
  document.getElementById("onboarding-skip").addEventListener("click", closeOnboarding);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeOnboarding();
  });

  document.getElementById("onboarding-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = document.getElementById("np-name").value.trim();
    if (!name) return;
    const ktru = document.getElementById("np-ktru").value.trim();
    const attrs = [...document.querySelectorAll(".attr-row")].map(row => ({
      key: row.querySelector(".attr-key").value.trim(),
      value: row.querySelector(".attr-value").value.trim()
    })).filter(a => a.key);

    const product = LK.addProduct({
      name,
      ktru: ktru ? [ktru] : [],
      category: "Новая категория",
      attributes: attrs,
      documents: [],
      delivery_regions: ["вся РФ"],
      search: { windowDays: 30, priceMin: 0, priceMax: 5000000, lotMode: "any",
        sources: ["ЕИС", "РТС-тендер", "РТС-маркет", "Сбербанк-АСТ", "Росэлторг"] }
    });
    closeOnboarding();
    renderProductSwitch();
    syncFiltersFromProduct();
    renderFeed();
    lkToast(`Товар «${product.name}» добавлен — проверяем закупки`);
  });

  // ---------- init ----------

  renderProductSwitch();
  syncFiltersFromProduct();
  renderFeed();

  if (LK.getProducts().length === 0) openOnboarding();

})();
