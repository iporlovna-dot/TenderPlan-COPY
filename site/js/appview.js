/* Рендер и интерактивность app.html — площадка поиска торгов.
   Точка входа = поиск по ключевым словам. Лента = карточки закупок.
   Сверка по ТЗ (score/checks) — надстройка, включается тумблером. */

(async function () {

  const params = new URLSearchParams(window.location.search);

  // ---------- гейт входа ----------

  await LK.initSession();  // если есть настоящая cookie-сессия — подтянуть компанию/избранное

  if (!LK.isLoggedIn()) {
    if (params.get("demo") === "1") {
      LK.seedDemo();
    } else {
      window.location.href = "login.html";
      return;
    }
  }

  const company = LK.getCompany();

  // доска закупок (общая по компании): статусы воронки и «до-победные» статусы,
  // для которых истёкший тендер = дохлый лид и убирается с доски (см. boardVisible)
  const BOARD_STATUSES = LK.BOARD_STATUSES;
  const PRE_AWARD = new Set(["Интересно", "Участвуем", "В просчёте"]);
  let employees = [];   // сотрудники компании — для выбора ответственного

  const DEFAULT_FILTERS = () => ({
    customer: "", region: "all", delivery: "all",
    law: "all", stage: "active",
    priceMin: 0, priceMax: null, source: "all", windowDays: 999
  });

  const state = {
    query: "",
    minus: "",
    searchId: null,          // id сохранённого поиска или null (свободный / «Все закупки»)
    view: "all",             // all | saved («Мои закупки»)
    boardStatus: "all",      // фильтр доски по статусу: all | один из BOARD_STATUSES
    page: 1,
    pageSize: LK.getPageSize(),
    filters: DEFAULT_FILTERS(),
    sort: "fresh",
    matchEnabled: false
  };

  // ---------- шапка / юзер ----------

  document.getElementById("user-company").textContent = company.name;
  document.getElementById("user-avatar").textContent =
    company.name.replace(/[^А-ЯA-Z]/g, "").slice(0, 2) || "ЛК";
  document.getElementById("plan-name").textContent =
    company.plan === "business" ? "Тариф «Бизнес»" :
    company.plan === "corp" ? "Тариф «Корпоративный»" : "Тариф «Старт»";
  if (company.planExpiresAt) {
    const d = new Date(company.planExpiresAt);
    if (!isNaN(d)) document.getElementById("plan-expires").textContent = "Активна до " + d.toLocaleDateString("ru-RU");
  }

  document.getElementById("logout-btn").addEventListener("click", async () => {
    await LK.apiLogout();
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
  document.querySelector('[data-quick="saved"]').addEventListener("click", (e) => {
    e.preventDefault();
    selectSaved();
  });

  // ---------- загрузка поиска в состояние ----------

  function loadSearch(id) {
    const s = LK.getSearches().find(x => x.id === id);
    if (!s) return;
    state.searchId = id;
    state.view = "all";
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
    state.view = "all";
    state.query = "";
    state.minus = "";
    state.filters = { ...DEFAULT_FILTERS(), stage: "all" };
    syncControlsFromState();
    renderSidebar();
    renderFeed();
  }

  function selectSaved() { selectSavedStatus("all"); }

  function selectSavedStatus(status) {
    state.searchId = null;
    state.view = "saved";
    state.boardStatus = status;
    state.query = "";
    state.minus = "";
    state.filters = { ...DEFAULT_FILTERS(), stage: "all" };
    syncControlsFromState();
    renderSidebar();
    renderBoardNav();
    renderFeed();
  }

  // видимость карточки на доске: до-победные статусы с истёкшим сроком — прочь
  // (дохлый лид), «Заключён контракт»/«Исполнен»/«Ждём оплату» держим независимо
  // от срока тендера (закупку выиграли — она остаётся в работе).
  function boardVisible(p) {
    const st = p.boardStatus || BOARD_STATUSES[0];
    return PRE_AWARD.has(st) ? (!isExpired(p) && liveStage(p) !== "completed") : true;
  }

  // sub-навигация в сайдбаре: статусы воронки со счётчиками
  function renderBoardNav() {
    const wrap = document.getElementById("board-nav");
    if (!wrap) return;
    const counts = {};
    BOARD_STATUSES.forEach(s => counts[s] = 0);
    LK.getSaved().filter(boardVisible).forEach(p => {
      const s = p.boardStatus || BOARD_STATUSES[0];
      counts[s] = (counts[s] || 0) + 1;
    });
    const onSaved = state.view === "saved";
    wrap.innerHTML = BOARD_STATUSES.map(s => `
      <a href="#" class="board-nav__item ${onSaved && state.boardStatus === s ? "is-active" : ""}" data-board="${lkEscape(s)}">
        <span class="board-nav__name">${lkEscape(s)}</span>
        ${counts[s] ? `<span class="board-nav__count">${counts[s]}</span>` : ""}
      </a>`).join("");
    wrap.querySelectorAll("[data-board]").forEach(el => el.addEventListener("click", (e) => {
      e.preventDefault();
      selectSavedStatus(el.dataset.board);
    }));
  }

  function refreshBoard() { updateSavedCount(); renderBoardNav(); }

  function updateSavedCount() {
    // бейдж = сколько закупок реально на доске (по той же логике boardVisible)
    const n = LK.getSaved().filter(boardVisible).length;
    const el = document.getElementById("saved-count");
    if (el) { el.textContent = n; el.style.display = n ? "inline-flex" : "none"; }
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

  // Регион приводим к субъекту РФ: район/город внутри области сворачиваем до самой
  // области, чтобы в фильтре не плодились «р-н Боровичский» и т.п. рядом с самой
  // «Новгородской областью». Клиентская нормализация текущего снапшота; карта
  // легко расширяется по мере появления новых районов в данных.
  const REGION_ROLLUP = {
    "р-н боровичский": "Новгородская область",
    "р-н хвойнинский": "Новгородская область",
    "р-н красновишерский": "Пермский край",
    "г. кемерово": "Кемеровская область - Кузбасс",
    "г. сургут": "Ханты-Мансийский автономный округ - Югра",
  };
  const FED_CITIES_RE = /^г\.?\s*(москва|санкт[- ]петербург|севастополь)$/i;
  const SUBJECT_RE = /(облас|край|республик|автономн|округ|кузбасс)/i;
  function canonicalRegion(r) {
    const s = (r || "").trim();
    if (!s) return "";
    const rolled = REGION_ROLLUP[s.toLowerCase()];
    if (rolled) return rolled;
    if (FED_CITIES_RE.test(s)) return s.replace(/^г\.?\s*/i, "г. ");
    if (SUBJECT_RE.test(s)) return s;   // уже субъект РФ — оставляем как есть
    return "";                          // район/город без известного субъекта → не мусорим фильтр
  }

  // регион и площадка — из реальных данных
  function populateFacets() {
    const all = LK.allPurchases();
    fillSelect("f-region", "all", "Вся РФ",
      [...new Set(all.map(p => canonicalRegion(p.region)).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")));
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
    state.searchId = null;          // свободный ввод «отвязывает» от шаблона (вид сохраняем)
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

  // Своё ТЗ живёт в localStorage как {name, terms[], savedAt}; в памяти держим
  // Set — сравнение идёт по всей ленте, а пересоздавать Set на каждую закупку
  // означало бы тысячи лишних аллокаций на каждую сортировку.
  let myTzSet = null;        // все требования моего ТЗ — для сверки с ТЗ закупки
  let mySubject = null;      // предмет: что за товар (топ основ по частоте × редкости)
  let corpusIdf = null;      // вес термина по редкости, считается из снапшота
  const matchMemo = new Map();   // id закупки → результат; сбрасывается при смене ТЗ
  const SUBJECT_SIZE = 8;

  // Корпус для оценки редкости — сам снапшот: у разобранных закупок есть tzTerms.
  // Считаем один раз при загрузке, потому что вес нужен на каждой закупке ленты.
  // Зачем вообще: замер на проде показал, что без веса два НИКАК не связанных ТЗ
  // совпадают на 16% (медиана), а 37% случайных пар дают больше 20% — на таком
  // шумовом полу любой показанный процент врёт. С весом пол падает до 6%.
  // Корпус берём из ТЗ, а не из названий закупок: проверено на живых данных —
  // на корпусе названий редкими оказываются как раз названия товаров, «перчатк»
  // вылетает из предмета, и в выдачу по ТЗ на перчатки лезут «ТОВАРЫ СПОРТИВНЫЕ».
  function buildCorpus() {
    const df = new Map();
    let docs = 0;
    LK.allPurchases().forEach(p => {
      if (!p.tzTerms || !p.tzTerms.length) return;
      docs++;
      new Set(p.tzTerms).forEach(t => df.set(t, (df.get(t) || 0) + 1));
    });
    corpusIdf = LKTZ.makeIdf(df, docs);
    matchMemo.clear();
  }

  function loadMyTz() {
    const tz = LK.getMyTz();
    // Варианты с беглой гласной раскрываем ТОЛЬКО здесь: в снапшоте они съедали
    // бы места («срок» тащил за собой «срк»), а пересечение множеств симметрично —
    // расширить одну сторону достаточно.
    myTzSet = tz && tz.terms && tz.terms.length
      ? LKTZ.expandVariants(new Set(tz.terms)) : null;
    // Предмет считаем по частотам. Старые ТЗ, сохранённые до этой версии, частот
    // не имеют — тогда предмета нет, и сверка честно работает только по
    // требованиям, пока человек не перезагрузит файл.
    mySubject = (tz && tz.freq && corpusIdf)
      ? LKTZ.subjectTerms(new Map(Object.entries(tz.freq)), corpusIdf, SUBJECT_SIZE)
      : null;
    matchMemo.clear();
    return tz;
  }

  function renderMatchBar() {
    const tz = loadMyTz();
    const toggle = document.getElementById("match-enable");
    const hint = document.getElementById("match-hint");
    const btn = document.getElementById("match-tz-btn");
    if (!myTzSet) {
      toggle.checked = false;
      toggle.disabled = true;
      state.matchEnabled = false;
      btn.textContent = "Загрузить своё ТЗ";
      hint.textContent = "— загрузите своё ТЗ, и лента оставит только закупки по вашему товару";
      document.getElementById("match-bar").classList.remove("is-on");
      return;
    }
    // ТЗ загружено — значит сверка нужна: включаем сразу, без лишнего клика.
    // И сразу ранжируем по совпадению: со включённой сверкой лента — это уже
    // результат поиска по ТЗ, а не общий список. В порядке «сначала новые»
    // закупки с 52% совпадения оказывались под одиннадцатипроцентными —
    // проверено на стенде с реальным ТЗ.
    toggle.disabled = false;
    toggle.checked = true;
    state.matchEnabled = true;
    if (state.sort === "fresh") {
      state.sort = "score";
      document.getElementById("sort-select").value = "score";
    }
    document.getElementById("match-bar").classList.add("is-on");
    btn.textContent = "Заменить ТЗ";
    // Показываем именно предмет: человеку важно видеть, ЧТО система в его ТЗ
    // считает товаром — если она ошиблась, это видно сразу, а не через пустую ленту.
    hint.textContent = mySubject && mySubject.length
      ? `— ищем по «${tz.name}»: ${mySubject.slice(0, 5).map(s => s.term).join(", ")}`
      : `— сверяем с «${tz.name}» (${tz.terms.length} требований)`;
  }

  document.getElementById("match-enable").addEventListener("change", (e) => {
    state.matchEnabled = e.target.checked;
    document.getElementById("match-bar").classList.toggle("is-on", state.matchEnabled);
    // при включении полезно сразу увидеть лучшее сверху, но выбор пользователя
    // не перетираем, если он уже сам задал сортировку по совпадению
    if (state.matchEnabled && state.sort !== "score") {
      state.sort = "score";
      document.getElementById("sort-select").value = "score";
    }
    renderFeed();
  });

  // Предмет закупки берём из названия и лотов, а не из её ТЗ: название есть у
  // ВСЕХ 4121 закупки, а разобранное ТЗ — у 4,6%. И название заказчик пишет сам,
  // канцелярита в нём нет — это уже готовый, курированный предмет.
  const stemCache = new Map();
  function purchaseStems(p) {
    let s = stemCache.get(p.id);
    if (!s) {
      s = LKTZ.stemSet([p.title, ...(p.lots || []).map(l => l.name)].filter(Boolean).join(" "));
      stemCache.set(p.id, s);
    }
    return s;
  }

  // Два сигнала, и они намеренно НЕ смешиваются в одно число.
  //   subject — про то ли это вообще товар (есть у каждой закупки);
  //   req     — насколько сходятся требования (только там, где ТЗ разобрано).
  // Слить их в один процент значило бы выдать «предмет не тот, зато шаблонные
  // формулировки совпали» за совпадение.
  function matchFor(p) {
    if (!state.matchEnabled || !myTzSet) return null;
    if (matchMemo.has(p.id)) return matchMemo.get(p.id);
    const subject = mySubject
      ? LKTZ.subjectMatch(mySubject, purchaseStems(p))
      : { score: 0, matched: [] };
    const req = (p.tzTerms && p.tzTerms.length)
      ? LKTZ.compareTerms(p.tzTerms, myTzSet, corpusIdf)
      : null;
    const m = { subject, req };
    matchMemo.set(p.id, m);
    return m;
  }

  // Со включённой сверкой лента становится поиском по своему ТЗ: показываем
  // только закупки, чей предмет совпал. Иначе «умная сверка» была бы просто
  // пересортировкой всех 4121 закупок, где подавляющее большинство — не про ваш товар.
  function passesMatch(p) {
    if (!state.matchEnabled || !mySubject) return true;
    const m = matchFor(p);
    return Boolean(m && m.subject.score > 0);
  }

  function subjectVerdict(score) {
    return score >= 60 ? "eligible" : score >= 25 ? "eligible_with_gaps" : "disqualified";
  }

  // Почему у закупки нет процента — человеку это важнее, чем пустое место.
  const TZ_STATUS_NOTE = {
    "no-doc": "у закупки не приложены документы — сверять нечего",
    // одна формулировка на два случая: не дошли до карточки за документами и
    // дошли, но не успели разобрать сам файл — для человека это одно и то же
    "pending": "ТЗ ещё не загружено — появится в одном из ближайших обновлений",
    "unsupported": "ТЗ приложено не в .docx — автоматически не разбирается",
    "empty": "в документе не нашлось текста (похоже на скан)",
    "error": "документ не удалось скачать",
  };
  function matchNote(p) {
    if (!state.matchEnabled || !myTzSet) return "";
    if (p.tzTerms && p.tzTerms.length) return "";
    // поля tzStatus нет вовсе — снапшот собран сборщиком до появления разбора ТЗ.
    // Это состояние выката, а не свойство закупки: обещать «недоступно» нечестно.
    return TZ_STATUS_NOTE[p.tzStatus] || TZ_STATUS_NOTE["pending"];
  }

  // ---------- фильтрация и поиск ----------

  // «слово» — любая пробежка букв/цифр; всё остальное (пробелы, запятые, дефисы,
  // №, кавычки) — просто разделитель. Одна и та же функция режет и запрос,
  // и текст закупки — чтобы по обе стороны сравнения было одинаково.
  function tokens(str) {
    return [...(str || "").toLowerCase().matchAll(/[а-яёa-z0-9]+/g)].map(m => m[0]);
  }
  // грубый стемминг: отсекаем типичное русское окончание (не ровно 2 буквы — это
  // «клинки» → «клин» ловило «клинику»/«клинический», совсем другие слова).
  // Список окончаний отсортирован от длинных к коротким, чтобы резать максимум.
  // Не лингвистика — временный приём для демо; на проде заменят морфоанализатор / эмбеддинги.
  const RU_ENDINGS = [
    "иями", "иях",
    "ами", "ями", "его", "ому", "ыми", "ими", "ого",
    "ах", "ях", "ов", "ев", "ий", "ый", "их", "ых", "ая", "яя", "ое", "ые", "ие", "ом", "ем", "им", "ым", "ой", "ей",
    "а", "я", "о", "е", "и", "ы", "у", "ю", "й", "ь",
  ];
  const STEM_MIN = 4;
  function stem(word) {
    const w = (word || "").toLowerCase();
    if (w.length <= STEM_MIN) return w;
    for (const suf of RU_ENDINGS) {
      if (w.length - suf.length >= STEM_MIN && w.endsWith(suf)) return w.slice(0, w.length - suf.length);
    }
    return w;
  }
  // «беглая гласная»: клинОк → клинка/клинки/клинков теряют «о» перед «к» в косвенных
  // формах и множественном числе — суффиксный стеммер выше этого не видит (буква
  // пропадает ВНУТРИ слова, а не в окончании). Нашли на живых данных: «клинки» в
  // запросе резался до стема «клинк», а в закупке было слово «клинок» (им. падеж,
  // ед. число) — не совпадало, хотя это тот же товар.
  // Аддитивно: только ДОБАВЛЯЕМ вариант без гласной как ещё один кандидат, не
  // заменяя исходное слово — так это не может сломать то, что уже совпадало.
  function fleetingVowelVariant(word) {
    const m = /^(.+[бвгджзйклмнпрстфхцчшщ])[оеё]к$/i.exec(word || "");
    return m ? m[1] + "к" : null;
  }
  function passesSearch(p) {
    // ОКПД2 сюда сознательно не включаем: это официальный классификатор, у него
    // название категории — часто длинный список разных товаров через запятую
    // («…перчатки и прочие аксессуары к одежде…»), из-за чего искали «перчатки»,
    // а находили галстуки — формальное совпадение, а не то, что реально покупают.
    // Заказчика сюда сознательно НЕ включаем — для этого есть отдельное поле
    // «Заказчик» в фильтрах (#f-customer, ниже). Раньше было title+customer+number,
    // из-за чего «поиск по товару» ловил вообще всё у организаций с таким словом
    // в названии: «школа» находило любую закупку любой «Школы №…» (мебель, ремонт
    // забора — к предмету поиска отношения не имело), «охрана» — закупки любой
    // «Инспекции по охране…» и т.д. Лоты добавили — там бывают более конкретные
    // названия позиций, которых нет в общем заголовке закупки (мульти-лотовые).
    const hay = (p.title + " " + (p.lots || []).map(l => l.name).join(" ") + " " + p.number).toLowerCase();
    const hayWords = tokens(hay);
    const plus = tokens(state.query);
    const minus = tokens(state.minus);
    // совпадение — по началу слова, а не по любому месту в строке: иначе
    // короткие запросы вроде «IT» ловят «Security»/«City» (буквы «it» просто
    // затесались посреди слова), а не только настоящие ИТ-закупки.
    // + беглая гласная в обе стороны: и если она пропала в запросе (стем «клинк»
    // из «клинки»), и если пропала в закупке (слово «клинок» само превращается
    // в «клинк» для сравнения).
    const matches = (w) => {
      const s = stem(w);
      const sAlt = fleetingVowelVariant(s);
      return hayWords.some(hw => hw.startsWith(s) || (sAlt && hw.startsWith(sAlt))
        || (() => { const hwAlt = fleetingVowelVariant(hw); return hwAlt && hwAlt.startsWith(s); })());
    };
    if (minus.some(matches)) return false;
    // все слова запроса должны найтись — иначе «строительные материалы» ловит
    // любую закупку со словом «материалы» (хоть горюче-смазочные)
    if (plus.length && !plus.every(matches)) return false;
    return true;
  }

  // ---------- аналитика (ценовой ориентир + история заказчика) ----------
  // Необязательный слой поверх снапшота (data/analytics.json, см.
  // tools/build_analytics.js) — реестр контрактов ЕИС, выборка по темам, не полный
  // реестр. Если файла нет — analytics === null и все функции ниже тихо отдают null.

  let _categoryDefs = null;
  function categoryDefs() {
    if (_categoryDefs) return _categoryDefs;
    const analytics = LK.getAnalytics();
    _categoryDefs = analytics
      ? Object.keys(analytics.categories).map(kw => ({ keyword: kw, stems: tokens(kw).map(stem) }))
      : [];
    return _categoryDefs;
  }
  // ищем тему поиска, все слова которой (по той же stem-логике, что и в поиске)
  // находятся в названии/позициях лота закупки — среди совпавших берём самую
  // «специфичную» (больше слов в теме), а не первую попавшуюся
  function categoryFor(p) {
    const analytics = LK.getAnalytics();
    const defs = categoryDefs();
    if (!analytics || !defs.length) return null;
    const hay = tokens(p.title + " " + (p.lots || []).map(l => l.name).join(" "));
    let best = null;
    for (const def of defs) {
      if (def.stems.every(s => hay.some(hw => hw.startsWith(s)))) {
        if (!best || def.stems.length > best.stems.length) best = def;
      }
    }
    if (!best) return null;
    const stats = analytics.categories[best.keyword];
    return (stats && stats.count) ? { keyword: best.keyword, stats } : null;
  }
  // та же нормализация имени заказчика, что и в tools/build_analytics.js —
  // в реестре контрактов ИНН заказчика в списке не показан, сопоставляем текстом
  function normalizeCustomerName(s) {
    return (s || "")
      .toUpperCase()
      .replace(/[«»"'ʼ]/g, "")
      .replace(/[^\wА-ЯЁ0-9]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }
  function customerFor(p) {
    const analytics = LK.getAnalytics();
    if (!analytics) return null;
    return analytics.customers[normalizeCustomerName(p.customer)] || null;
  }

  function passesFilters(p) {
    const f = state.filters;
    if (f.customer) {
      const hay = (p.customer + " " + (p.customerInn || "")).toLowerCase();
      if (!hay.includes(f.customer.toLowerCase())) return false;
    }
    if (f.region !== "all" && canonicalRegion(p.region) !== f.region) return false;
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
  // Неконкурентная закупка (223-ФЗ «Иной способ» / единственный поставщик): дата
  // окончания подачи формальная, реальных торгов нет — не показываем ложный отсчёт.
  function isCompetitive(p) { return p.competitive !== false; }

  // Точный остаток до конца подачи (дни/часы/минуты) — от текущего момента по endDate.
  function remainingText(p) {
    if (!p.endDate) return "";
    const ms = new Date(p.endDate).getTime() - Date.now();
    if (ms <= 0) return "срок истёк";
    const d = Math.floor(ms / DAY_MS);
    const h = Math.floor((ms % DAY_MS) / 3600000);
    const mi = Math.floor((ms % 3600000) / 60000);
    if (d >= 1) return `${d} ${lkPlural(d, ["день","дня","дней"])} ${h} ${lkPlural(h, ["час","часа","часов"])}`;
    if (h >= 1) return `${h} ${lkPlural(h, ["час","часа","часов"])} ${mi} ${lkPlural(mi, ["минуту","минуты","минут"])}`;
    return `${mi} ${lkPlural(mi, ["минуту","минуты","минут"])}`;
  }

  // Ссылка «Открыть на площадке». Битые ссылки ЕИС 44-ФЗ, застрявшие в текущем
  // снапшоте (printForm/listModal.html — модалка печати, не карточка), подменяем на
  // печатную форму view.html — она хотя бы показывает саму закупку. Новый снапшот
  // уже отдаёт корректный common-info (см. tools/sources/eis.js).
  function platformHref(p) {
    const h = p.href || "";
    return h.replace(/printForm\/listModal\.html/i, "printForm/view.html");
  }

  function highlight(text) {
    let out = lkEscape(text);
    const plus = tokens(state.query);
    plus.forEach(w => {
      const s = stem(w);
      // беглая гласная: стем «клинк» (из «клинки») должен подсветить и «клинок» в
      // тексте — если стем оканчивается на «согласная+к», вставляем перед этой «к»
      // необязательную о/е/ё (см. fleetingVowelVariant — здесь обратная операция).
      const m = /^(.+[бвгджзйклмнпрстфхцчшщ])к$/i.exec(s);
      const core = m
        ? m[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[оеёОЕЁ]?к"
        : s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      // подсвечиваем слово целиком, начиная от стема (перчато́к, нитрило́вых)
      out = out.replace(new RegExp("(" + core + "[а-яёa-z0-9]*)", "gi"), "<mark>$1</mark>");
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

  // Порог «горящего» срока: до трёх суток включительно. Такую закупку ещё
  // реально успеть подать, но откладывать уже нельзя — именно её и стоит
  // подсвечивать. Оформление класса — в CSS (.deadline.is-urgent).
  const URGENT_DAYS = 3;

  function deadlineText(p) {
    // у неконкурентных «до конца подачи» вводит в заблуждение — торгов нет
    if (!isCompetitive(p)) return `<span class="deadline muted">неконкурентная закупка</span>`;
    if (liveStage(p) === "active") {
      const urgent = daysLeft(p) <= URGENT_DAYS ? " is-urgent" : "";
      if (p.endDate) return `<span class="deadline${urgent}">до конца подачи: <b>${remainingText(p)}</b></span>`;
      const d = Math.max(1, daysLeft(p));
      return `<span class="deadline${urgent}"><b>${d}</b> ${lkPlural(d, ["день","дня","дней"])} до конца подачи</span>`;
    }
    return `<span class="deadline muted">${STAGE[liveStage(p)].label}</span>`;
  }

  function analyticsDetail(p) {
    const cat = categoryFor(p);
    const cust = customerFor(p);
    if (!cat && !cust) return "";

    const catRow = cat ? (() => {
      const s = cat.stats;
      const diff = (p.price && s.medianPrice) ? Math.round((p.price - s.medianPrice) / s.medianPrice * 100) : null;
      const diffCls = diff == null ? "" : diff > 5 ? "is-high" : diff < -5 ? "is-low" : "";
      const diffTxt = diff == null ? "" :
        ` <span class="analytics-diff ${diffCls}">${diff > 0 ? "+" : ""}${diff}% к медиане по рынку</span>`;
      return `
        <div class="analytics-row">
          <span class="analytics-row__k">Ценовой ориентир</span>
          <span class="analytics-row__v">по теме «${lkEscape(cat.keyword)}»: медиана ${lkFormatMoney(s.medianPrice)},
            среднее ${lkFormatMoney(s.avgPrice)}, диапазон ${lkFormatMoney(s.minPrice)}–${lkFormatMoney(s.maxPrice)}
            (${s.count} контрактов)${diffTxt}</span>
        </div>`;
    })() : "";

    const custRow = cust ? `
        <div class="analytics-row">
          <span class="analytics-row__k">История заказчика</span>
          <span class="analytics-row__v">${cust.count} ${lkPlural(cust.count, ["контракт", "контракта", "контрактов"])}
            в выборке, средний чек ${lkFormatMoney(cust.avgPrice)}${cust.minDate ? `, с ${cust.minDate.slice(0, 4)} по ${(cust.maxDate || cust.minDate).slice(0, 4)}` : ""}</span>
        </div>` : "";

    return `
      <div class="detail-block detail-analytics">
        <div class="detail-block__title">Аналитика по заключённым контрактам</div>
        ${catRow}
        ${custRow}
        <div class="analytics-note">По выборке контрактов ЕИС (не полный реестр); уровень конкуренции пока не считаем — эти данные не публикуются в списке, только в протоколе каждой закупки отдельно.</div>
      </div>`;
  }

  // количество товара + единица измерения («6 шт», «12 000 пар»); «—» если неизвестно
  function lotQty(l) {
    const q = (l.qty == null ? "" : String(l.qty)).trim();
    if (!q || q === "—") return "—";
    return l.unit ? `${lkEscape(q)} ${lkEscape(l.unit)}` : lkEscape(q);
  }
  // цена за ЕДИНИЦУ: Портал уже отдаёт costPerUnit (l.price = за единицу); у ЕИС и
  // прочих l.price — сумма строки, делим на количество, если оно числовое; иначе «—».
  function lotUnitPrice(p, l) {
    const isPortal = /mos\.ru|Портал/i.test(p.source || "");
    if (isPortal) return l.price;
    const qn = parseFloat(String(l.qty).replace(/\s/g, "").replace(",", "."));
    if (qn > 0 && isFinite(qn)) return l.price / qn;
    return null;
  }

  // компактный селект статуса в шапке карточки (триаж прямо из ленты):
  // «＋ На доску» = не на доске; выбор статуса добавляет/меняет, пустое — снимает
  function boardStatusSelect(p) {
    const st = LK.savedStatus(p.id);
    return `<select class="board-select ${st ? "is-on" : ""}" data-board-status="${p.id}" title="Статус на доске компании">
      <option value="" ${st ? "" : "selected"}>＋ На доску</option>
      ${BOARD_STATUSES.map(s => `<option value="${lkEscape(s)}" ${st === s ? "selected" : ""}>${lkEscape(s)}</option>`).join("")}
    </select>`;
  }

  // чип с ответственным в шапке карточки (если назначен)
  function boardAssigneeChip(p) {
    const s = LK.getSaved().find(x => x.id === p.id);
    return s && s.assigneeName ? `<span class="assignee-chip" title="Ответственный за закупку">👤 ${lkEscape(s.assigneeName)}</span>` : "";
  }

  // блок «Работа с закупкой» в детали — только если закупка на доске: смена
  // статуса, назначение ответственного (из сотрудников компании), снятие с доски
  function boardControls(p) {
    const st = LK.savedStatus(p.id);
    if (!st) return "";
    const saved = LK.getSaved().find(x => x.id === p.id);
    const assigneeId = saved ? saved.assigneeId : null;
    const assignOpts = `<option value="">— не назначен —</option>` +
      employees.map(e => `<option value="${e.id}" ${String(assigneeId) === String(e.id) ? "selected" : ""}>${lkEscape(e.name)}</option>`).join("");
    return `
      <div class="detail-block detail-board">
        <div class="detail-block__title">Работа с закупкой</div>
        <div class="board-row">
          <label class="board-field"><span>Статус</span>
            <select class="board-select" data-board-status="${p.id}">
              ${BOARD_STATUSES.map(s => `<option value="${lkEscape(s)}" ${st === s ? "selected" : ""}>${lkEscape(s)}</option>`).join("")}
            </select>
          </label>
          <label class="board-field"><span>Ответственный</span>
            <select class="board-assignee" data-assignee="${p.id}" ${employees.length ? "" : "disabled"}>${assignOpts}</select>
          </label>
          <button class="btn btn-ghost btn-sm board-remove" data-board-remove="${p.id}">Убрать с доски</button>
        </div>
        ${employees.length ? "" : `<div class="board-hint">Чтобы назначать ответственного, добавьте сотрудников в личном кабинете.</div>`}
      </div>`;
  }

  function purchaseDetail(p, m) {
    const lotsHead = `
      <div class="lot-line lot-head">
        <span class="lot-line__idx"></span>
        <span class="lot-line__name">Позиция</span>
        <span class="lot-line__qty">Количество товара</span>
        <span class="lot-line__price">Цена за единицу</span>
      </div>`;
    const lotsRows = p.lots.map((l, i) => {
      const up = lotUnitPrice(p, l);
      return `
      <div class="lot-line">
        <span class="lot-line__idx">${p.lots.length > 1 ? (i + 1) + "." : "•"}</span>
        <span class="lot-line__name">${lkEscape(l.name)}</span>
        <span class="lot-line__qty">${lotQty(l)}</span>
        <span class="lot-line__price">${up == null ? "—" : lkFormatMoney(up)}</span>
      </div>`;
    }).join("");
    const lots = lotsHead + lotsRows;

    const facts = [
      ["НМЦК", lkFormatMoney(p.price)],
      ["Обеспечение заявки", p.guaranteeApp ? lkFormatMoney(p.guaranteeApp) : "не требуется"],
      ["Обеспечение контракта", p.guaranteeContract ? lkFormatMoney(p.guaranteeContract) : "не требуется"],
      ["Аванс", p.prepayment ? p.prepayment + "%" : "нет"],
      ...(p.procedureType ? [["Способ закупки", p.procedureType + (isCompetitive(p) ? "" : " · неконкурентная")]] : []),
      ["Срок поставки", p.deliveryDays != null ? `${p.deliveryDays} ${lkPlural(p.deliveryDays, ["день","дня","дней"])}` : "—"],
      ["Опубликована", p.publishedDaysAgo === 0 ? "сегодня" : `${p.publishedDaysAgo} ${lkPlural(p.publishedDaysAgo, ["день","дня","дней"])} назад`],
      ["Место поставки", p.deliveryPlace || canonicalRegion(p.region) || "—"]
    ].map(([k, v]) => `<div class="fact"><span class="fact__k">${k}</span><span class="fact__v">${lkEscape(String(v))}</span></div>`).join("");

    const docsHtml = (p.documents && p.documents.length) ? `
        <div class="detail-block">
          <div class="detail-block__title">Документы (${p.documents.length})</div>
          <div class="doc-list">
            ${p.documents.map(d => `<a class="doc-link" href="${lkEscape(d.url)}" target="_blank" rel="noopener noreferrer"><span class="doc-ic">📎</span><span class="doc-name">${lkEscape(d.name)}</span><span class="doc-dl">скачать ↓</span></a>`).join("")}
          </div>
        </div>` : "";

    // Внутренняя обёртка нужна для плавного разворачивания: контейнер
    // анимирует grid-template-rows 0fr→1fr, а обёртка гасит переполнение.
    // Без неё пришлось бы дёргать display, а его анимировать нельзя.
    return `
      <div class="tender-detail">
        <div class="tender-detail__inner">
          ${boardControls(p)}
          <div class="detail-block">
            <div class="detail-block__title">Позиции лота${p.lots.length > 1 ? ` (${p.lots.length})` : ""}</div>
            ${lots}
          </div>
          <div class="detail-block">
            <div class="detail-block__title">Условия закупки</div>
            <div class="facts-grid">${facts}</div>
          </div>
          ${docsHtml}
          ${matchDetail(p, m)}
          <div class="tender-actions">
            ${p.href
              ? `<a class="btn btn-primary btn-sm" href="${lkEscape(platformHref(p))}" target="_blank" rel="noopener noreferrer">Открыть на площадке ↗</a>`
              : `<span class="btn btn-primary btn-sm" style="opacity:.5;cursor:not-allowed;" title="Ссылка на площадку недоступна">Ссылка недоступна</span>`}
            <a class="btn btn-ghost btn-sm" data-tz="${p.id}" href="#">Сверить своё ТЗ</a>
          </div>
        </div>
      </div>`;
  }

  // Разбор совпадения внутри карточки: сам процент без объяснения бесполезен —
  // человеку нужно видеть, ЧТО именно совпало и чего не хватает.
  function matchDetail(p, m) {
    if (!state.matchEnabled || !myTzSet || !m) return "";

    const chips = (arr, cls, limit = 10) =>
      arr.slice(0, limit).map(t => `<span class="term-chip ${cls}">${lkEscape(t)}</span>`).join("")
      + (arr.length > limit ? `<span class="term-more">и ещё ${arr.length - limit}</span>` : "");

    const subj = !mySubject
      ? `<div class="tz-hint">Ваше ТЗ сохранено старой версией — загрузите файл заново, чтобы искать по предмету.</div>`
      : m.subject.matched.length
        ? `<div class="match-row"><span class="match-row__k">Предмет совпал</span>
             <span class="match-row__v">${chips(m.subject.matched, "is-ok")}</span></div>`
        : `<div class="match-row"><span class="match-row__k">Предмет</span>
             <span class="match-row__v muted">слов вашего ТЗ в названии закупки нет</span></div>`;

    // Требования разбираем только там, где ТЗ закупки реально скачано и разобрано.
    // Иначе честно говорим почему, а не показываем ноль как результат сверки.
    let req;
    if (!m.req) {
      req = `<div class="tz-hint">${lkEscape(matchNote(p) || "ТЗ закупки ещё не разобрано")}</div>`;
    } else {
      const words = m.req.missing.filter(t => !LKTZ.isMeasured(t));
      req = `
        <div class="explanation">${lkEscape(m.req.explanation)}</div>
        ${m.req.measured.covered.length ? `<div class="match-row"><span class="match-row__k">Числовые требования, которые вы покрываете</span>
          <span class="match-row__v">${chips(m.req.measured.covered, "is-ok")}</span></div>` : ""}
        ${m.req.measured.missing.length ? `<div class="match-row"><span class="match-row__k">Числовых требований нет в вашем ТЗ</span>
          <span class="match-row__v">${chips(m.req.measured.missing, "is-gap")}</span></div>` : ""}
        ${words.length ? `<div class="match-row"><span class="match-row__k">Чего ещё нет у вас</span>
          <span class="match-row__v">${chips(words, "is-gap", 12)}</span></div>` : ""}`;
    }

    return `<div class="detail-block detail-match">
      <div class="detail-block__title">Сверка с вашим ТЗ${p.tzDoc ? ` — по «${lkEscape(p.tzDoc)}»` : ""}</div>
      ${subj}
      ${req}
    </div>`;
  }

  function cardHtml(p) {
    const m = matchFor(p);
    const fresh = p.publishedDaysAgo <= 2 ? `<span class="badge badge-fresh">новая</span>` : "";
    const lot = p.lotNote ? `<span class="lot-note">позиция ${p.lotNote.position} из ${p.lotNote.total}</span>` : "";
    const note = matchNote(p);
    // Крупно — предмет: он есть у каждой закупки и отвечает на главный вопрос
    // «это вообще про мой товар?». Мелкой строкой — требования, если ТЗ закупки
    // разобрано; если нет, прочерк с причиной в подсказке, а не выдуманный ноль.
    // Причина в колонку шириной 78px не влезает, её место — в детали карточки.
    const scoreCol = m
      ? `<div class="tender-score">
           <div class="tender-score__num ${verdictClass(subjectVerdict(m.subject.score))}">${m.subject.score}<span class="tender-score__pct">%</span></div>
           <span class="tender-score__cap">предмет</span>
           ${m.req
             ? `<span class="tender-score__req" title="совпадение по требованиям разобранного ТЗ закупки">ТЗ ${m.req.score}%</span>`
             : `<span class="tender-score__req tender-score__req--none" title="${lkEscape(note)}">ТЗ —</span>`}
         </div>`
      : "";

    return `
    <article class="tender-card ${m ? "has-match" : ""} ${LK.isViewed(p.id) ? "is-viewed" : ""}" data-id="${p.id}">
      <div class="tender-card__main">
        ${scoreCol}
        <div class="tender-body">
          <div class="tender-body__top">
            <span class="badge badge-law ${p.law === "223-ФЗ" ? "is-223" : ""}">${p.law}</span>
            ${isCompetitive(p)
              ? `<span class="badge badge-stage ${STAGE[liveStage(p)].cls}">${STAGE[liveStage(p)].label}</span>`
              : `<span class="badge badge-stage stage-noncomp" title="${lkEscape(p.procedureType || "неконкурентная закупка")}">Неконкурентная</span>`}
            <span class="badge badge-source">${lkEscape(p.source)}</span>
            ${fresh}${lot}
            <span class="board-head-controls">${boardAssigneeChip(p)}${boardStatusSelect(p)}</span>
          </div>
          <h3 class="tender-title">${highlight(p.title)}</h3>
          <div class="tender-meta">
            <span><b>№${p.number}</b></span>
            <span>${lkEscape(p.customer)}</span>
            ${canonicalRegion(p.region) ? `<span>${lkEscape(canonicalRegion(p.region))}</span>` : ""}
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

  document.getElementById("page-size-select").value = String(state.pageSize);
  document.getElementById("page-size-select").addEventListener("change", (e) => {
    state.pageSize = Number(e.target.value) || 30;
    LK.setPageSize(state.pageSize);
    renderFeed();
  });

  // окно номеров страниц вокруг текущей + первая/последняя, с «…» на разрывах
  function pageWindow(cur, total) {
    const set = new Set([1, total, cur - 1, cur, cur + 1].filter(n => n >= 1 && n <= total));
    const arr = [...set].sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    arr.forEach(n => { if (prev && n - prev > 1) out.push("…"); out.push(n); prev = n; });
    return out;
  }

  function renderPagination(page, totalPages) {
    const nav = document.getElementById("feed-pagination");
    if (totalPages <= 1) { nav.innerHTML = ""; return; }
    nav.innerHTML = `
      <button class="page-btn page-arrow" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} aria-label="Предыдущая">‹</button>
      ${pageWindow(page, totalPages).map(n => n === "…"
        ? `<span class="page-ellipsis">…</span>`
        : `<button class="page-btn ${n === page ? "is-current" : ""}" data-page="${n}">${n}</button>`).join("")}
      <button class="page-btn page-arrow" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""} aria-label="Следующая">›</button>`;
    nav.querySelectorAll("[data-page]").forEach(btn => {
      btn.addEventListener("click", () => {
        const n = Number(btn.dataset.page);
        if (!n || n < 1 || n > totalPages || n === state.page) return;
        state.page = n;
        renderFeed(false);
        document.getElementById("feed-header").scrollIntoView({ block: "start" });
      });
    });
  }

  function renderFeed(resetPage = true) {
    if (resetPage) state.page = 1;
    const feed = document.getElementById("feed-content");
    const title = document.getElementById("feed-title");
    const count = document.getElementById("feed-count");

    const current = state.searchId ? LK.getSearches().find(s => s.id === state.searchId) : null;
    const savedView = state.view === "saved";
    title.textContent = savedView
      ? (state.boardStatus === "all" ? "★ Мои закупки" : "★ " + state.boardStatus)
      : current ? current.name : (state.query ? `Поиск: «${state.query}»` : "Все закупки");

    // лента — без просроченных/завершённых; доска — по boardVisible + выбранному статусу
    const notDone = p => !isExpired(p) && liveStage(p) !== "completed";
    let list = savedView
      ? LK.getSaved().filter(boardVisible)
          .filter(p => state.boardStatus === "all" || (p.boardStatus || BOARD_STATUSES[0]) === state.boardStatus)
          .filter(passesSearch)
      : LK.allPurchases().filter(notDone).filter(passesSearch).filter(passesFilters).filter(passesMatch);

    const sortFn = {
      fresh: (a, b) => a.publishedDaysAgo - b.publishedDaysAgo,
      deadline: (a, b) => rank(a) - rank(b) || daysLeft(a) - daysLeft(b),
      "price-desc": (a, b) => b.price - a.price,
      "price-asc": (a, b) => a.price - b.price,
      score: (a, b) => (score(b) - score(a)) || a.publishedDaysAgo - b.publishedDaysAgo
    }[state.sort];
    function rank(p) { return liveStage(p) === "active" ? 0 : 1; }
    // Сначала предмет, и лишь при равном предмете — совпадение требований:
    // закупка про ваш товар с неразобранным ТЗ полезнее, чем чужая с разобранным.
    function score(p) {
      const m = matchFor(p);
      if (!m) return -1;
      return m.subject.score * 100 + (m.req ? m.req.score : 0);
    }

    list.sort(sortFn);

    // Пустая лента сразу после загрузки ТЗ — самый обидный тупик: человек не
    // понимает, фильтр это или закупок правда нет. Называем причину и показываем,
    // ЧТО именно система сочла предметом — ошибку видно сразу.
    const emptyByMatch = !savedView && state.matchEnabled && mySubject && mySubject.length;
    if (!list.length) {
      count.textContent = savedView ? "" : "ничего не найдено";
      feed.innerHTML = `<div class="empty-state">
        <h3>${savedView
          ? (state.boardStatus === "all" ? "Пока нет закупок на доске" : `В статусе «${lkEscape(state.boardStatus)}» пусто`)
          : emptyByMatch ? "Закупок по вашему ТЗ сейчас нет" : "Ничего не найдено"}</h3>
        <p>${savedView
          ? "В карточке закупки выберите статус (＋ На доску), чтобы добавить её в работу."
          : emptyByMatch
            ? `Искали по предмету: ${lkEscape(mySubject.slice(0, 5).map(s => s.term).join(", "))}. Выключите «Умную сверку», чтобы вернуть всю ленту, или загрузите ТЗ, где товар назван прямее.`
            : "Под текущие ключевые слова и фильтры закупок нет. Попробуйте убрать минус-слова, расширить регион или сменить этап."}</p>
      </div>`;
      document.getElementById("feed-pagination").innerHTML = "";
      return;
    }

    const totalPages = Math.max(1, Math.ceil(list.length / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    if (state.page < 1) state.page = 1;

    count.textContent =
      `${list.length} ${lkPlural(list.length, ["закупка","закупки","закупок"])}` +
      (!savedView && state.matchEnabled && mySubject && mySubject.length ? " по вашему ТЗ" : "") +
      (totalPages > 1 ? ` · страница ${state.page} из ${totalPages}` : "");

    const start = (state.page - 1) * state.pageSize;
    const shownList = list.slice(start, start + state.pageSize);
    feed.innerHTML = shownList.map(cardHtml).join("");
    renderPagination(state.page, totalPages);

    const byId = {};
    shownList.forEach(p => { byId[p.id] = p; });

    feed.querySelectorAll(".tender-card__main").forEach(el => {
      el.addEventListener("click", () => {
        const card = el.closest(".tender-card");
        card.classList.toggle("is-open");
        if (card.classList.contains("is-open")) {
          LK.markViewed(card.dataset.id);
          card.classList.add("is-viewed");
        }
      });
    });
    // доска: перерисовать ленту, сохранив раскрытую карточку (если она осталась)
    function afterBoardChange(el) {
      const card = el.closest(".tender-card");
      const openId = card && card.classList.contains("is-open") ? card.dataset.id : null;
      refreshBoard();
      renderFeed(false);
      if (openId) {
        const again = feed.querySelector('.tender-card[data-id="' + openId + '"]');
        if (again) again.classList.add("is-open");
      }
    }
    // статус на доске (селект в шапке и в детали): пусто = снять, статус = добавить/сменить
    feed.querySelectorAll("[data-board-status]").forEach(sel => {
      sel.addEventListener("click", (e) => e.stopPropagation());
      sel.addEventListener("change", (e) => {
        e.stopPropagation();
        const id = sel.dataset.boardStatus, p = byId[id], val = sel.value;
        if (!val) LK.removeSaved(id);
        else if (LK.isSaved(id)) LK.setBoardStatus(id, val);
        else if (p) LK.addToBoard(p, val);
        afterBoardChange(sel);
      });
    });
    // ответственный
    feed.querySelectorAll("[data-assignee]").forEach(sel => {
      sel.addEventListener("click", (e) => e.stopPropagation());
      sel.addEventListener("change", (e) => {
        e.stopPropagation();
        const opt = sel.options[sel.selectedIndex];
        LK.setBoardAssignee(sel.dataset.assignee, sel.value, opt ? opt.textContent : null);
        afterBoardChange(sel);
      });
    });
    // снять с доски
    feed.querySelectorAll("[data-board-remove]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        LK.removeSaved(btn.dataset.boardRemove);
        afterBoardChange(btn);
      });
    });
    // сверить своё ТЗ
    feed.querySelectorAll("[data-tz]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        openTzModal(byId[btn.dataset.tz]);
      });
    });
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

  // ---------- модалка: моё ТЗ (для сверки по всей ленте) ----------

  const myTzModal = document.getElementById("mytz-modal");
  function openMyTzModal() {
    const tz = LK.getMyTz();
    document.getElementById("mytz-file").value = "";
    document.getElementById("mytz-text").value = "";
    document.getElementById("mytz-current").textContent = tz
      ? `Сейчас загружено: «${tz.name}» — ${tz.terms.length} требований`
      : "Пока ничего не загружено.";
    document.getElementById("mytz-remove").style.display = tz ? "" : "none";
    document.getElementById("mytz-result").innerHTML = "";
    myTzModal.classList.add("is-open");
  }
  function closeMyTzModal() { myTzModal.classList.remove("is-open"); }
  document.getElementById("match-tz-btn").addEventListener("click", openMyTzModal);
  document.getElementById("mytz-cancel").addEventListener("click", closeMyTzModal);
  myTzModal.addEventListener("click", (e) => { if (e.target === myTzModal) closeMyTzModal(); });

  document.getElementById("mytz-remove").addEventListener("click", () => {
    LK.setMyTz(null);
    renderMatchBar();
    renderFeed();
    closeMyTzModal();
    lkToast("Своё ТЗ убрано");
  });

  document.getElementById("mytz-save").addEventListener("click", async () => {
    const res = document.getElementById("mytz-result");
    const file = document.getElementById("mytz-file").files[0];
    const typed = document.getElementById("mytz-text").value.trim();
    let text, name;
    res.innerHTML = `<div class="tz-hint">Читаю…</div>`;
    try {
      if (file) { text = await LKTZ.extractText(file); name = file.name; }
      else { text = typed; name = "вставленный текст"; }
    } catch (e) {
      res.innerHTML = `<div class="tz-hint tz-err">${lkEscape(e.message)}</div>`;
      return;
    }
    if (!text || text.length < 20) {
      res.innerHTML = `<div class="tz-hint tz-err">Слишком мало текста — загрузите .docx/.txt или вставьте описание товара.</div>`;
      return;
    }
    // Храним и частоты: именно по ним определяется предмет ТЗ (что за товар), а
    // не по длине слов. Объект на ~1500 основ — это десятки килобайт, лимит
    // localStorage (~5 МБ на домен) выдерживает с запасом.
    const freqMap = LKTZ.termFreq(text);
    const terms = [...freqMap.keys()];
    if (!terms.length) {
      res.innerHTML = `<div class="tz-hint tz-err">Не удалось выделить требования из этого текста.</div>`;
      return;
    }
    LK.setMyTz({ name, terms, freq: Object.fromEntries(freqMap), savedAt: new Date().toISOString() });
    renderMatchBar();
    // включаем сразу: человек грузил ТЗ именно ради процентов, лишний клик тут лишний
    document.getElementById("match-enable").checked = true;
    document.getElementById("match-enable").dispatchEvent(new Event("change"));
    closeMyTzModal();
    lkToast(`ТЗ загружено — ${terms.length} требований`);
  });

  // ---------- модалка: сверка своего ТЗ с ТЗ закупки ----------

  const tzModal = document.getElementById("tz-modal");
  let tzCurrentPurchase = null;
  function tenderText(p) {
    return [p.title, ...(p.lots || []).map(l => `${l.name} ${l.okpd || ""}`), p.okpd].filter(Boolean).join("\n");
  }
  function openTzModal(p) {
    if (!p) return;
    tzCurrentPurchase = p;
    document.getElementById("tz-modal-sub").textContent = `Закупка №${p.number} — ${p.title}`.slice(0, 170);
    document.getElementById("tz-my-file").value = "";
    document.getElementById("tz-my-text").value = "";
    document.getElementById("tz-p-file").value = "";
    document.getElementById("tz-p-text").value = tenderText(p);
    document.getElementById("tz-result").innerHTML = "";
    tzModal.classList.add("is-open");
  }
  function closeTzModal() { tzModal.classList.remove("is-open"); }
  document.getElementById("tz-cancel").addEventListener("click", closeTzModal);
  tzModal.addEventListener("click", (e) => { if (e.target === tzModal) closeTzModal(); });

  async function readSide(fileId, textId) {
    const f = document.getElementById(fileId).files[0];
    if (f) return await LKTZ.extractText(f);
    return document.getElementById(textId).value.trim();
  }
  document.getElementById("tz-run").addEventListener("click", async () => {
    const res = document.getElementById("tz-result");
    res.innerHTML = `<div class="tz-hint">Читаю файлы и сравниваю…</div>`;
    let myText, pText;
    try { myText = await readSide("tz-my-file", "tz-my-text"); }
    catch (e) { res.innerHTML = `<div class="tz-hint tz-err">Ваше ТЗ: ${lkEscape(e.message)}</div>`; return; }
    try { pText = await readSide("tz-p-file", "tz-p-text"); }
    catch (e) { res.innerHTML = `<div class="tz-hint tz-err">ТЗ закупки: ${lkEscape(e.message)}</div>`; return; }
    if (document.getElementById("tz-my-file").files[0]) document.getElementById("tz-my-text").value = myText;
    if (document.getElementById("tz-p-file").files[0]) document.getElementById("tz-p-text").value = pText;
    if (!myText || myText.length < 5) { res.innerHTML = `<div class="tz-hint tz-err">Добавьте своё ТЗ (файл .txt/.docx или текст).</div>`; return; }
    if (!pText || pText.length < 5) { res.innerHTML = `<div class="tz-hint tz-err">Добавьте ТЗ закупки.</div>`; return; }

    const m = LKTZ.compare(pText, myText);
    if (LK.isServerSession() && tzCurrentPurchase) {
      LK.apiSend("POST", "/api/tz-checks", {
        purchaseId: String(tzCurrentPurchase.id || ""),
        purchaseNumber: tzCurrentPurchase.number || "",
        purchaseTitle: tzCurrentPurchase.title || "",
        score: m.score, verdict: m.verdict,
      }).catch(() => {});
    }
    const checks = m.checks.map(c => `
      <div class="check-row"><span>${lkEscape(c.req)}${c.note ? ` — <span class="check-note">${lkEscape(c.note)}</span>` : ""}</span>
      <span class="check-status ${checkClass(c.status)}">${checkIcon(c.status)}</span></div>`).join("");
    res.innerHTML = `
      <div class="tz-verdict">
        <div class="tz-score ${verdictClass(m.verdict)}">${m.score}<span>%</span></div>
        <div style="flex:1;">${verdictBadge(m.verdict)}
          <div class="explanation" style="margin-top:8px;">${lkEscape(m.explanation)}</div></div>
      </div>
      <div class="detail-block" style="margin-top:12px;">${checks || "<div class='tz-hint'>Требования из текста не выделены.</div>"}</div>`;
  });

  // ---------- init ----------

  renderMatchBar();
  renderSidebar();

  employees = await LK.getEmployees();  // для селекта ответственного в карточках
  renderBoardNav();

  document.getElementById("feed-count").textContent = "загружаем закупки…";

  updateSavedCount();

  LK.loadPurchases().then(() => {
    buildCorpus();     // редкость терминов — из самого снапшота, до первой сверки
    loadMyTz();        // предмет считается уже с корпусом
    renderMatchBar();
    populateFacets();  // регион/площадка из реальных данных
    // по умолчанию показываем реальные «Все закупки»; сохранённые поиски — в сайдбаре
    selectAllPurchases();
    // живое устаревание: раз в минуту перерисовываем (без сброса пагинации) и
    // обновляем счётчики доски — чтобы истёкшие лиды уходили и из чисел
    setInterval(() => { renderFeed(false); refreshBoard(); }, 60000);
  });
  // аналитика — необязательный слой, грузится параллельно и не блокирует ленту;
  // если файла нет/не собрался, analyticsFor() просто вернёт null везде
  LK.loadAnalytics().then(() => renderFeed(false));

})();
