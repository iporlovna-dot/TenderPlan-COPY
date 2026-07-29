/* ЛЕКАЛО — демо-логика площадки поиска торгов.
   Никакого бэкенда: состояние живёт в localStorage этого браузера.

   Модель перевёрнута: точка входа — ПОИСК по ключевым словам, лента = закупки
   (44-ФЗ / 223-ФЗ) со всех площадок. Умная сверка по ТЗ (score/verdict/checks) —
   НАДСТРОЙКА поверх закупки, включается тумблером «сверить с товаром».

   Реальные данные придут из прямой интеграции с ЕИС (см. plan.md). Здесь — моки,
   воспроизводящие формат: карточка закупки + опциональный MatchResult по товару. */

const LK = (() => {

  const KEY_COMPANY  = "lekalo_company";
  const KEY_PRODUCTS = "lekalo_products";
  const KEY_SEARCHES = "lekalo_searches";
  const KEY_CURRENT_SEARCH = "lekalo_current_search";

  // ---------- компания / сессия ----------

  function getCompany() {
    try { return JSON.parse(localStorage.getItem(KEY_COMPANY)); } catch { return null; }
  }
  function setCompany(c) { localStorage.setItem(KEY_COMPANY, JSON.stringify(c)); }
  const KEY_SAVED = "lekalo_saved";
  const KEY_VIEWED = "lekalo_viewed";
  const KEY_PAGE_SIZE = "lekalo_page_size";
  function clearSession() {
    localStorage.removeItem(KEY_COMPANY);
    localStorage.removeItem(KEY_PRODUCTS);
    localStorage.removeItem(KEY_SEARCHES);
    localStorage.removeItem(KEY_CURRENT_SEARCH);
    localStorage.removeItem(KEY_SAVED);
    localStorage.removeItem(KEY_VIEWED);
  }

  // ---------- сколько закупок показывать на странице ----------

  function getPageSize() {
    const n = Number(localStorage.getItem(KEY_PAGE_SIZE));
    return n === 10 || n === 30 ? n : 30;
  }
  function setPageSize(n) { localStorage.setItem(KEY_PAGE_SIZE, String(n)); }

  // ---------- доска закупок (общая по компании: статус воронки + ответственный) ----------
  // Кэш в localStorage — массив закупок с полями boardStatus/assigneeId/assigneeName
  // (их отдаёт GET /api/saved). В демо/локально всё живёт только в этом кэше; при
  // серверной сессии мутаторы дополнительно шлют POST/PATCH/DELETE на /api/saved.

  const BOARD_STATUSES = ["Интересно", "Участвуем", "В просчёте", "Заключён контракт", "Исполнен", "Ждём оплату"];

  function getSaved() {
    try { return JSON.parse(localStorage.getItem(KEY_SAVED)) || []; } catch { return []; }
  }
  function _setSaved(list) { localStorage.setItem(KEY_SAVED, JSON.stringify(list)); }
  function isSaved(id) { return getSaved().some(p => p.id === id); }
  function savedStatus(id) {  // текущий статус карточки на доске или null, если её там нет
    const p = getSaved().find(x => x.id === id);
    return p ? (p.boardStatus || BOARD_STATUSES[0]) : null;
  }

  function addToBoard(p, status) {
    status = BOARD_STATUSES.includes(status) ? status : BOARD_STATUSES[0];
    const list = getSaved();
    const i = list.findIndex(x => x.id === p.id);
    if (i >= 0) list[i].boardStatus = status;
    else list.unshift({ ...p, boardStatus: status, assigneeId: null, assigneeName: null });
    _setSaved(list);
    if (hasServerSession) apiSend("POST", "/api/saved", { purchase: p, status }).catch(() => {});
    return status;
  }
  function setBoardStatus(id, status) {
    if (!BOARD_STATUSES.includes(status)) return;
    const list = getSaved(); const p = list.find(x => x.id === id);
    if (!p) return;
    p.boardStatus = status; _setSaved(list);
    if (hasServerSession) apiSend("PATCH", "/api/saved/" + encodeURIComponent(id), { status }).catch(() => {});
  }
  function setBoardAssignee(id, assigneeId, assigneeName) {
    const list = getSaved(); const p = list.find(x => x.id === id);
    if (!p) return;
    p.assigneeId = (assigneeId == null || assigneeId === "") ? null : Number(assigneeId);
    p.assigneeName = p.assigneeId == null ? null : (assigneeName || null);
    _setSaved(list);
    if (hasServerSession) apiSend("PATCH", "/api/saved/" + encodeURIComponent(id), { assignee_id: p.assigneeId }).catch(() => {});
  }
  function removeSaved(id) {
    _setSaved(getSaved().filter(x => x.id !== id));
    if (hasServerSession) apiSend("DELETE", "/api/saved/" + encodeURIComponent(id)).catch(() => {});
  }

  // сотрудники компании — для выбора ответственного (кэш на сессию)
  let _employees = null;
  async function getEmployees() {
    if (_employees) return _employees;
    if (!hasServerSession) { _employees = []; return _employees; }
    try { _employees = await apiGet("/api/employees"); } catch { _employees = []; }
    return _employees;
  }

  // ---------- просмотренные закупки (чтобы не путаться в ленте) ----------

  function getViewed() {
    try { return JSON.parse(localStorage.getItem(KEY_VIEWED)) || []; } catch { return []; }
  }
  function isViewed(id) { return getViewed().includes(id); }
  function markViewed(id) {
    if (isViewed(id)) return;
    const list = getViewed();
    list.push(id);
    // не даём списку расти бесконечно
    if (list.length > 4000) list.splice(0, list.length - 4000);
    localStorage.setItem(KEY_VIEWED, JSON.stringify(list));
  }
  function isLoggedIn() { return !!getCompany(); }

  // ---------- реальная серверная сессия (личный кабинет) ----------
  // Демо (?demo=1, LK.seedDemo) работает целиком в localStorage, без сервера —
  // это НЕ трогаем. Если на бэкенде (см. server/app/accounts.py) есть настоящая
  // сессия по cookie, initSession() подтягивает компанию + избранное в тот же
  // localStorage-кэш, а мутаторы доски (addToBoard/setBoardStatus/…) и сверка ТЗ
  // дополнительно шлют изменения на сервер.

  let hasServerSession = false;

  async function apiGet(path) {
    const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }
  async function apiSend(method, path, body) {
    const r = await fetch(path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || ("http " + r.status));
    }
    return r.json().catch(() => ({}));
  }

  async function initSession() {
    try {
      const me = await apiGet("/api/auth/me");
      setCompany({
        name: me.company.name, inn: me.company.inn, plan: me.company.plan,
        planExpiresAt: me.company.planExpiresAt,
        userId: me.user.id, userName: me.user.name, userEmail: me.user.email, role: me.user.role,
      });
      hasServerSession = true;
      try {
        const saved = await apiGet("/api/saved");
        localStorage.setItem(KEY_SAVED, JSON.stringify(saved));
      } catch { /* избранное недоступно — оставляем локальный кэш как есть */ }
      try {
        const searches = await apiGet("/api/searches");
        localStorage.setItem(KEY_SEARCHES, JSON.stringify(searches));
      } catch { /* сохранённые поиски недоступны — оставляем локальный кэш как есть */ }
    } catch {
      hasServerSession = false;  // демо или ещё не вошли — обычный локальный режим
    }
  }

  async function apiLogout() {
    if (hasServerSession) {
      try { await apiSend("POST", "/api/auth/logout"); } catch { /* всё равно чистим локально */ }
    }
  }
  function isServerSession() { return hasServerSession; }

  // ---------- товары (для надстройки «сверка по ТЗ») ----------

  function getProducts() {
    try { return JSON.parse(localStorage.getItem(KEY_PRODUCTS)) || []; } catch { return []; }
  }
  function setProducts(list) { localStorage.setItem(KEY_PRODUCTS, JSON.stringify(list)); }
  function addProduct(p) {
    const list = getProducts();
    p.id = "prod_" + Math.random().toString(36).slice(2, 9);
    list.push(p);
    setProducts(list);
    return p;
  }

  // ---------- сохранённые поиски (шаблоны, как «Мои поиски» у ТП) ----------

  function getSearches() {
    try { return JSON.parse(localStorage.getItem(KEY_SEARCHES)) || []; } catch { return []; }
  }
  function setSearches(list) { localStorage.setItem(KEY_SEARCHES, JSON.stringify(list)); }
  function addSearch(s) {
    const list = getSearches();
    s.id = "srch_" + Math.random().toString(36).slice(2, 9);
    list.push(s);
    setSearches(list);
    if (hasServerSession) apiSend("POST", "/api/searches", { ...s }).catch(() => {});
    return s;
  }
  function updateSearch(id, patch) {
    const list = getSearches();
    const i = list.findIndex(s => s.id === id);
    if (i >= 0) {
      list[i] = { ...list[i], ...patch };
      setSearches(list);
      if (hasServerSession) apiSend("PATCH", "/api/searches/" + id, patch).catch(() => {});
      return list[i];
    }
    return null;
  }
  function deleteSearch(id) {
    setSearches(getSearches().filter(s => s.id !== id));
    if (localStorage.getItem(KEY_CURRENT_SEARCH) === id) localStorage.removeItem(KEY_CURRENT_SEARCH);
    if (hasServerSession) apiSend("DELETE", "/api/searches/" + id).catch(() => {});
  }
  function getCurrentSearchId() { return localStorage.getItem(KEY_CURRENT_SEARCH) || null; }
  function setCurrentSearchId(id) {
    if (id) localStorage.setItem(KEY_CURRENT_SEARCH, id);
    else localStorage.removeItem(KEY_CURRENT_SEARCH);
  }

  // ---------- demo seed ----------

  function seedDemo() {
    setCompany({
      name: "ООО «МедСнаб»",
      inn: "7701234567",
      email: "demo@medsnab.example",
      plan: "business",
      created_at: new Date().toISOString().slice(0, 10)
    });
    setProducts([
      {
        id: "prod_gloves",
        name: "Перчатки смотровые нитриловые, неопудренные, синие",
        ktru: ["32.50.50.190-00001458"],
        category: "Расходники — перчатки",
        attributes: [
          { key: "материал", value: "нитрил", critical: true },
          { key: "стерильность", value: "нет" },
          { key: "опудренность", value: "нет" },
          { key: "размеры", value: "XS, S, M, L", status: "declared" },
          { key: "толщина_пальцы_мм", value: "0.11", status: "confirmable" }
        ],
        documents: ["Регистрационное удостоверение"],
        delivery_regions: ["вся РФ"]
      },
      {
        id: "prod_blade",
        name: "Клинок ларингоскопа Миллер, прямой, №2, KaWe",
        ktru: ["32.50.13.190-00007686"],
        category: "Медтехника — ЛОР/анестезиология",
        attributes: [
          { key: "тип_освещения", value: "фиброоптика", critical: true },
          { key: "форма_клинка", value: "прямой", critical: true },
          { key: "совместимость", value: "ISO 7376", status: "declared" },
          { key: "одноразовость", value: "нет, многоразовый" },
          { key: "материал", value: "нержавеющая сталь" }
        ],
        documents: ["Регистрационное удостоверение", "Сертификат соответствия"],
        delivery_regions: ["вся РФ"]
      }
    ]);
    setSearches([
      {
        id: "srch_gloves",
        name: "Перчатки нитриловые",
        query: "перчатки нитриловые",
        minus: "боксёрские латексные",
        filters: { law: "all", region: "all", stage: "active", priceMin: 0, priceMax: 5000000,
          windowDays: 30, sources: ["ЕИС", "РТС-тендер", "Сбербанк-АСТ", "Росэлторг", "РТС-маркет"] },
        newCount: 3
      },
      {
        id: "srch_laryngo",
        name: "Ларингоскопы и клинки",
        query: "ларингоскоп клинок",
        minus: "",
        filters: { law: "all", region: "all", stage: "all", priceMin: 0, priceMax: 3000000,
          windowDays: 45, sources: ["ЕИС", "РТС-тендер", "Росэлторг", "Сбербанк-АСТ", "РТС-маркет"] },
        newCount: 1
      }
    ]);
    setCurrentSearchId("srch_gloves");
  }

  // ---------- моковые закупки (реестр площадки) ----------
  // Стандартная карточка закупки + опциональный matches{productId → MatchResult}.
  // matches заполнен только там, где имеет смысл сверять с демо-товарами.

  const MOCK_PURCHASES = [
      {
        id: "p1", number: "0372200034526000112",
        title: "Поставка перчаток смотровых нитриловых неопудренных для нужд стационара",
        customer: "ГБУЗ «Городская клиническая больница №7»",
        law: "44-ФЗ", source: "ЕИС", region: "г. Москва",
        okpd: "32.50.50.190", price: 1840000,
        stage: "active", deadlineDays: 6, publishedDaysAgo: 2,
        guaranteeApp: 18400, guaranteeContract: 92000, prepayment: 0,
        lots: [{ name: "Перчатки нитриловые смотровые", qty: "45 000 пар", price: 1840000 }],
        matches: {
          prod_gloves: {
            score: 100, verdict: "eligible",
            checks: [
              { req: "материал = нитрил", status: "pass" },
              { req: "стерильность = нет", status: "pass" },
              { req: "опудренность = нет", status: "pass" },
              { req: "размеры: S, M, L", status: "pass" },
              { req: "толщина в области пальцев ≥ 0,11 мм", status: "pass" }
            ],
            explanation: "Проходите по всем характеристикам ТЗ. Пробелов нет — можно подавать заявку без уточнений."
          }
        }
      },
      {
        id: "p2", number: "0158200004526000871",
        title: "Поставка перчаток медицинских смотровых нитриловых неопудренных",
        customer: "КГБУЗ «Краевая клиническая больница»",
        law: "44-ФЗ", source: "РТС-тендер", region: "Алтайский край",
        okpd: "32.50.50.190", price: 640000,
        stage: "active", deadlineDays: 14, publishedDaysAgo: 4,
        guaranteeApp: 6400, guaranteeContract: 32000, prepayment: 30,
        lots: [{ name: "Перчатки нитриловые (XS–XL)", qty: "16 000 пар", price: 640000 }],
        matches: {
          prod_gloves: {
            score: 92, verdict: "eligible_with_gaps",
            checks: [
              { req: "материал = нитрил", status: "pass" },
              { req: "опудренность = нет", status: "pass" },
              { req: "размеры: XS, S, M, L, XL", status: "gap", note: "в карточке нет XL — подтвердите наличие" },
              { req: "толщина в области пальцев ≥ 0,10 мм", status: "pass" }
            ],
            explanation: "Всё техническое сходится. Один пробел: заказчик просит размер XL, в карточке товара его нет — подтвердите наличие перед подачей."
          }
        }
      },
      {
        id: "p3", number: "0373200019526000045",
        title: "Поставка перчаток латексных смотровых опудренных",
        customer: "ГБУЗ «Станция скорой медицинской помощи»",
        law: "44-ФЗ", source: "Сбербанк-АСТ", region: "г. Санкт-Петербург",
        okpd: "32.50.50.190", price: 410000,
        stage: "active", deadlineDays: 9, publishedDaysAgo: 1,
        guaranteeApp: 4100, guaranteeContract: 20500, prepayment: 0,
        lots: [{ name: "Перчатки латексные опудренные", qty: "12 000 пар", price: 410000 }],
        matches: {
          prod_gloves: {
            score: 38, verdict: "disqualified",
            checks: [
              { req: "материал = латекс", status: "fail", note: "в карточке нитрил — жёсткое несоответствие" },
              { req: "опудренность = да", status: "fail", note: "в карточке товар неопудренный" },
              { req: "размеры: S, M, L", status: "pass" }
            ],
            explanation: "Дисквалификация: заказчику нужны латексные опудренные перчатки, а не нитриловые. Материал — обязательный параметр, замены не допускаются по ТЗ."
          }
        }
      },
      {
        id: "p4", number: "0844200001126000230",
        title: "Поставка расходных материалов: перчатки нитриловые, маски, шапочки (сборный лот)",
        customer: "БУЗ УР «Республиканская клиническая больница»",
        law: "44-ФЗ", source: "ЕИС", region: "Удмуртская Республика",
        okpd: "32.50.50.190", price: 2260000,
        stage: "active", deadlineDays: 21, publishedDaysAgo: 3,
        guaranteeApp: 22600, guaranteeContract: 113000, prepayment: 0,
        lots: [
          { name: "Перчатки нитриловые смотровые", qty: "50 000 пар", price: 1400000 },
          { name: "Маски медицинские трёхслойные", qty: "120 000 шт", price: 560000 },
          { name: "Шапочки одноразовые", qty: "80 000 шт", price: 300000 }
        ],
        lotNote: { position: 1, total: 3 },
        matches: {
          prod_gloves: {
            score: 97, verdict: "eligible",
            checks: [
              { req: "материал = нитрил", status: "pass" },
              { req: "стерильность = нет", status: "pass" },
              { req: "толщина в области пальцев ≥ 0,11 мм", status: "pass" }
            ],
            explanation: "Сборный лот из 3 позиций — ваш товар закрывает позицию №1. Подаётесь на весь лот, но проходите именно по нужной вам позиции."
          }
        }
      },
      {
        id: "p5", number: "0872300007826000067",
        title: "Поставка перчаток нитриловых химически стойких для лаборатории контроля качества",
        customer: "ФБУЗ «Центр гигиены и эпидемиологии»",
        law: "223-ФЗ", source: "РТС-маркет", region: "Новосибирская область",
        okpd: "32.50.50.190", price: 275000,
        stage: "active", deadlineDays: 4, publishedDaysAgo: 1,
        guaranteeApp: 0, guaranteeContract: 13750, prepayment: 0,
        lots: [{ name: "Перчатки нитриловые химстойкие", qty: "6 000 пар", price: 275000 }],
        matches: {
          prod_gloves: {
            score: 100, verdict: "eligible",
            checks: [
              { req: "материал = нитрил", status: "pass" },
              { req: "химстойкость к спиртам", status: "pass" },
              { req: "размеры: S, M, L", status: "pass" }
            ],
            explanation: "Полное совпадение по всем пунктам ТЗ. Срок подачи короткий — успевайте за 4 дня."
          }
        }
      },
      {
        id: "p6", number: "0351200000726000985",
        title: "Поставка клинков ларингоскопических для отделения анестезиологии-реанимации",
        customer: "ГБУЗ «Областная клиническая больница»",
        law: "44-ФЗ", source: "ЕИС", region: "Свердловская область",
        okpd: "32.50.13.190", price: 560000,
        stage: "active", deadlineDays: 11, publishedDaysAgo: 5,
        guaranteeApp: 5600, guaranteeContract: 28000, prepayment: 0,
        lots: [{ name: "Клинок ларингоскопа прямой, фиброоптика", qty: "40 шт", price: 560000 }],
        matches: {
          prod_blade: {
            score: 89, verdict: "eligible",
            checks: [
              { req: "совместимость = ISO 7376", status: "pass", note: "ТЗ: «Flexline» — семантически то же крепление" },
              { req: "форма = прямая", status: "pass" },
              { req: "тип освещения = волоконная оптика", status: "pass" },
              { req: "многоразовый, автоклавируемый", status: "pass" }
            ],
            explanation: "Проходите: ТЗ использует термин «Flexline», в карточке — ISO 7376, это одно и то же крепление. Форма и оптика совпадают полностью."
          }
        }
      },
      {
        id: "p7", number: "0128200000926000341",
        title: "Поставка набора клинков к ларингоскопу: прямые и изогнутые",
        customer: "ГБУЗ «Детская краевая клиническая больница»",
        law: "44-ФЗ", source: "Росэлторг", region: "Приморский край",
        okpd: "32.50.13.190", price: 310000,
        stage: "committee", deadlineDays: 0, publishedDaysAgo: 20,
        guaranteeApp: 3100, guaranteeContract: 15500, prepayment: 0,
        lots: [{ name: "Набор клинков ларингоскопа (прямые + изогнутые)", qty: "20 наборов", price: 310000 }],
        matches: {
          prod_blade: {
            score: 71, verdict: "eligible_with_gaps",
            checks: [
              { req: "форма = прямая, есть в наборе", status: "pass" },
              { req: "форма = изогнутая, есть в наборе", status: "gap", note: "в карточке только прямой клинок" },
              { req: "тип освещения = волоконная оптика", status: "pass" }
            ],
            explanation: "Заказчик просит набор из прямого и изогнутого клинков. У вас в карточке только прямой — по прямому пройдёте, по набору в целом будет пробел."
          }
        }
      },
      {
        id: "p8", number: "0166200002326000104",
        title: "Поставка ларингоскопов с одноразовыми клинками (полный комплект)",
        customer: "ГБУЗ «Городская больница скорой помощи»",
        law: "44-ФЗ", source: "ЕИС", region: "Ростовская область",
        okpd: "32.50.13.190", price: 1120000,
        stage: "active", deadlineDays: 8, publishedDaysAgo: 2,
        guaranteeApp: 11200, guaranteeContract: 56000, prepayment: 0,
        lots: [{ name: "Ларингоскоп с одноразовыми клинками", qty: "30 комплектов", price: 1120000 }],
        matches: {
          prod_blade: {
            score: 22, verdict: "disqualified",
            checks: [
              { req: "одноразовость = да, обязательна", status: "fail", note: "в карточке многоразовый клинок" },
              { req: "комплект с рукоятью", status: "gap" }
            ],
            explanation: "Дисквалификация: заказчику нужны одноразовые клинки без права повторной стерилизации — жёсткое требование, многоразовый вариант не подойдёт."
          }
        }
      },
      {
        id: "p9", number: "0356200000426000512",
        title: "Поставка изделий медицинского назначения для оториноларингологии",
        customer: "ГБУЗ «Клиническая больница №4»",
        law: "223-ФЗ", source: "Сбербанк-АСТ", region: "Воронежская область",
        okpd: "32.50.13.190", price: 890000,
        stage: "active", deadlineDays: 23, publishedDaysAgo: 6,
        guaranteeApp: 0, guaranteeContract: 44500, prepayment: 15,
        lots: [{ name: "Клинки ларингоскопические, срок службы ≥ 5 лет", qty: "60 шт", price: 890000 }],
        matches: {
          prod_blade: {
            score: 76, verdict: "eligible_with_gaps",
            checks: [
              { req: "совместимость = ISO 7376", status: "pass" },
              { req: "форма = прямая", status: "pass" },
              { req: "срок службы ≥ 5 лет, документально", status: "gap", note: "нет протокола испытаний в карточке" }
            ],
            explanation: "Технически подходите. Заказчик просит документальное подтверждение срока службы — добавьте протокол в карточку, чтобы закрыть пробел."
          }
        }
      },
      // --- закупки других категорий: площадка ищет ЛЮБЫЕ торги, не только медицину ---
      {
        id: "p10", number: "0173200001426000778",
        title: "Поставка бумаги офисной А4, класс C, 80 г/м²",
        customer: "Администрация городского округа",
        law: "44-ФЗ", source: "РТС-тендер", region: "г. Москва",
        okpd: "17.12.14.110", price: 320000,
        stage: "active", deadlineDays: 12, publishedDaysAgo: 3,
        guaranteeApp: 3200, guaranteeContract: 16000, prepayment: 0,
        lots: [{ name: "Бумага А4 80 г/м²", qty: "4 000 пачек", price: 320000 }],
        matches: {}
      },
      {
        id: "p11", number: "0119300012826000091",
        title: "Выполнение работ по текущему ремонту кровли здания школы",
        customer: "МБОУ «Средняя общеобразовательная школа №12»",
        law: "44-ФЗ", source: "ЕИС", region: "Краснодарский край",
        okpd: "43.91.19.000", price: 4750000,
        stage: "active", deadlineDays: 17, publishedDaysAgo: 4,
        guaranteeApp: 47500, guaranteeContract: 475000, prepayment: 0,
        lots: [{ name: "Текущий ремонт кровли (1 200 м²)", qty: "1 объект", price: 4750000 }],
        matches: {}
      },
      {
        id: "p12", number: "0348100004526000203",
        title: "Поставка канцелярских товаров и расходных материалов для офиса",
        customer: "ФКУ «Центр обеспечения деятельности»",
        law: "44-ФЗ", source: "Росэлторг", region: "г. Санкт-Петербург",
        okpd: "17.23.13.190", price: 540000,
        stage: "active", deadlineDays: 5, publishedDaysAgo: 1,
        guaranteeApp: 5400, guaranteeContract: 27000, prepayment: 0,
        lots: [{ name: "Канцтовары (набор из 42 позиций)", qty: "1 партия", price: 540000 }],
        matches: {}
      },
      {
        id: "p13", number: "0173100008826000340",
        title: "Оказание услуг по поставке и настройке серверного оборудования",
        customer: "ГКУ «Информационные технологии региона»",
        law: "223-ФЗ", source: "РТС-тендер", region: "Свердловская область",
        okpd: "26.20.14.000", price: 8900000,
        stage: "active", deadlineDays: 26, publishedDaysAgo: 7,
        guaranteeApp: 0, guaranteeContract: 890000, prepayment: 20,
        lots: [{ name: "Серверы стоечные + пусконаладка", qty: "6 ед.", price: 8900000 }],
        matches: {}
      },
      {
        id: "p14", number: "0362200005926000058",
        title: "Поставка дезинфицирующих средств и антисептиков для рук",
        customer: "ГБУЗ «Инфекционная клиническая больница»",
        law: "44-ФЗ", source: "Сбербанк-АСТ", region: "Новосибирская область",
        okpd: "20.20.14.000", price: 470000,
        stage: "committee", deadlineDays: 0, publishedDaysAgo: 15,
        guaranteeApp: 4700, guaranteeContract: 23500, prepayment: 0,
        lots: [{ name: "Кожный антисептик, 1 л", qty: "3 000 флаконов", price: 470000 }],
        matches: {}
      },
      {
        id: "p15", number: "0326200015826000019",
        title: "Поставка перчаток нитриловых текстурированных на пальцах для процедурных кабинетов",
        customer: "ГАУЗ «Краевая клиническая больница №1»",
        law: "44-ФЗ", source: "РТС-тендер", region: "Краснодарский край",
        okpd: "32.50.50.190", price: 980000,
        stage: "active", deadlineDays: 27, publishedDaysAgo: 5,
        guaranteeApp: 9800, guaranteeContract: 49000, prepayment: 0,
        lots: [{ name: "Перчатки нитриловые текстурированные", qty: "24 000 пар", price: 980000 }],
        matches: {
          prod_gloves: {
            score: 84, verdict: "eligible_with_gaps",
            checks: [
              { req: "материал = нитрил", status: "pass" },
              { req: "текстура на пальцах = да", status: "gap", note: "в карточке не указано — уточните у производителя" },
              { req: "длина манжеты ≥ 240 мм", status: "pass" }
            ],
            explanation: "Похоже, подходите. Не хватает данных по текстуре пальцев в карточке — стоит внести один раз и закрыть пробел для будущих закупок."
          }
        }
      },
      {
        id: "p16", number: "0134300004526000612",
        title: "Поставка мебели офисной (столы, кресла, шкафы) для административного здания",
        customer: "Департамент имущественных отношений",
        law: "44-ФЗ", source: "ЕИС", region: "Ростовская область",
        okpd: "31.01.12.000", price: 1650000,
        stage: "completed", deadlineDays: 0, publishedDaysAgo: 40,
        guaranteeApp: 16500, guaranteeContract: 82500, prepayment: 0,
        lots: [{ name: "Комплект офисной мебели", qty: "1 партия", price: 1650000 }],
        matches: {}
      }
    ];

  // реальные закупки грузятся из data/purchases.json (снапшот Портала поставщиков),
  // моки — фолбэк для file:// и офлайна.
  let _purchases = null;
  async function loadPurchases() {
    const api = (typeof window !== "undefined" && window.LK_API_BASE) || "";
    const urls = [];
    if (api) urls.push(api.replace(/\/$/, "") + "/api/purchases?take=60");
    urls.push("data/purchases.json");          // снапшот (Pages)
    for (const u of urls) {
      try {
        const r = await fetch(u, { cache: "no-store" });
        if (!r.ok) continue;
        const d = await r.json();
        if (d && Array.isArray(d.purchases) && d.purchases.length) {
          _purchases = d.purchases;
          return allPurchases();
        }
      } catch (e) { /* пробуем следующий источник */ }
    }
    return allPurchases();                       // фолбэк — моки (file:///офлайн)
  }
  function allPurchases() { return _purchases || MOCK_PURCHASES; }

  // Аналитика (ценовой ориентир по категориям + история заказчика) — необязательный
  // слой поверх снапшота, отдельный файл data/analytics.json (собирается
  // tools/build_analytics.js из реестра контрактов ЕИС). Если файла нет/не собрался —
  // просто не показываем блок аналитики, не роняем остальную ленту.
  let _analytics = null;
  async function loadAnalytics() {
    try {
      const r = await fetch("data/analytics.json", { cache: "no-store" });
      if (r.ok) _analytics = await r.json();
    } catch (e) { /* аналитика необязательна — тихо пропускаем */ }
    return _analytics;
  }
  function getAnalytics() { return _analytics; }

  return {
    getCompany, setCompany, clearSession, isLoggedIn,
    getProducts, setProducts, addProduct,
    getSearches, setSearches, addSearch, updateSearch, deleteSearch,
    getCurrentSearchId, setCurrentSearchId,
    getSaved, isSaved, savedStatus, addToBoard, setBoardStatus, setBoardAssignee, removeSaved,
    getEmployees, BOARD_STATUSES,
    getViewed, isViewed, markViewed,
    getPageSize, setPageSize,
    initSession, apiLogout, isServerSession, apiGet, apiSend,
    seedDemo, allPurchases, loadPurchases, loadAnalytics, getAnalytics
  };
})();

// ---------- общие утилиты ----------

function lkFormatMoney(n) {
  return new Intl.NumberFormat("ru-RU").format(n) + " ₽";
}
function lkEscape(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function lkToast(msg) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = document.createElement("div");
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  requestAnimationFrame(() => t.classList.add("is-visible"));
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("is-visible"), 2600);
}

// склонение слова по числу: lkPlural(5, ["день","дня","дней"])
function lkPlural(n, forms) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

// ---------- состояние шапки лендинга (index.html) ----------

function lkInitHeaderState() {
  const cta = document.querySelector("[data-header-cta]");
  if (!cta) return;
  if (LK.isLoggedIn()) {
    cta.innerHTML = `<a class="btn btn-ghost btn-sm" href="app.html">В приложение →</a>`;
    const loginLink = document.querySelector("[data-header-login]");
    if (loginLink) loginLink.style.display = "none";
  }
}
