/* ЛЕКАЛО — демо-логика. Никакого бэкенда: состояние живёт в localStorage
   этого браузера. Моковые закупки ниже иллюстрируют формат вердикта,
   описанный в plan.md (MatchResult): score, verdict, checks, explanation. */

const LK = (() => {

  const KEY_COMPANY = "lekalo_company";
  const KEY_PRODUCTS = "lekalo_products";
  const KEY_CURRENT = "lekalo_current_product";

  // ---------- storage ----------

  function getCompany() {
    try { return JSON.parse(localStorage.getItem(KEY_COMPANY)); } catch { return null; }
  }
  function setCompany(c) { localStorage.setItem(KEY_COMPANY, JSON.stringify(c)); }
  function clearSession() {
    localStorage.removeItem(KEY_COMPANY);
    localStorage.removeItem(KEY_PRODUCTS);
    localStorage.removeItem(KEY_CURRENT);
  }
  function isLoggedIn() { return !!getCompany(); }

  function getProducts() {
    try { return JSON.parse(localStorage.getItem(KEY_PRODUCTS)) || []; } catch { return []; }
  }
  function setProducts(list) { localStorage.setItem(KEY_PRODUCTS, JSON.stringify(list)); }
  function addProduct(p) {
    const list = getProducts();
    p.id = "prod_" + Math.random().toString(36).slice(2, 9);
    list.push(p);
    setProducts(list);
    setCurrentProduct(p.id);
    return p;
  }
  function getCurrentProduct() {
    const list = getProducts();
    if (!list.length) return null;
    const id = localStorage.getItem(KEY_CURRENT);
    return list.find(p => p.id === id) || list[0];
  }
  function setCurrentProduct(id) { localStorage.setItem(KEY_CURRENT, id); }

  // ---------- demo seed (кнопка "смотреть демо без регистрации") ----------

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
        delivery_regions: ["вся РФ"],
        search: { windowDays: 21, priceMin: 0, priceMax: 5000000, lotMode: "any",
          sources: ["ЕИС", "РТС-тендер", "Сбербанк-АСТ"] }
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
        delivery_regions: ["вся РФ"],
        search: { windowDays: 25, priceMin: 0, priceMax: 3000000, lotMode: "any",
          sources: ["ЕИС", "РТС-тендер", "Росэлторг"] }
      }
    ]);
    setCurrentProduct("prod_gloves");
  }

  // ---------- mock tender feed ----------
  // score/verdict/checks воспроизводят реальные прогоны движка, описанные в plan.md §2

  function allTenders() {
    return [
      {
        id: "t1", productId: "prod_gloves",
        title: "Поставка перчаток смотровых нитриловых для нужд ГБУЗ",
        number: "0372200034526000112", customer: "ГБУЗ «Городская клиническая больница №7»",
        source: "ЕИС", region: "г. Москва", price: 1840000, deadlineDays: 6,
        ktru: "32.50.50.190-00001458", verdict: "eligible", score: 100,
        checks: [
          { req: "материал = нитрил", status: "pass" },
          { req: "стерильность = нет", status: "pass" },
          { req: "опудренность = нет", status: "pass" },
          { req: "размеры: S, M, L", status: "pass" },
          { req: "толщина в области пальцев ≥ 0,11 мм", status: "pass" }
        ],
        explanation: "Проходите по всем характеристикам ТЗ. Пробелов нет — можно подавать заявку без уточнений."
      },
      {
        id: "t2", productId: "prod_gloves",
        title: "Поставка перчаток медицинских смотровых нитриловых неопудренных",
        number: "0158200004526000871", customer: "КГБУЗ «Краевая клиническая больница»",
        source: "РТС-тендер", region: "Алтайский край", price: 640000, deadlineDays: 14,
        ktru: "32.50.50.190-00001458", verdict: "eligible_with_gaps", score: 92,
        checks: [
          { req: "материал = нитрил", status: "pass" },
          { req: "опудренность = нет", status: "pass" },
          { req: "размеры: XS, S, M, L, XL", status: "gap", note: "в карточке нет XL — подтвердите наличие" },
          { req: "толщина в области пальцев ≥ 0,10 мм", status: "pass" }
        ],
        explanation: "Всё техническое сходится. Один пробел: заказчик просит размер XL, в карточке товара его нет — подтвердите наличие перед подачей."
      },
      {
        id: "t3", productId: "prod_gloves",
        title: "Поставка перчаток латексных смотровых опудренных",
        number: "0373200019526000045", customer: "ГБУЗ «Станция скорой медицинской помощи»",
        source: "Сбербанк-АСТ", region: "г. Санкт-Петербург", price: 410000, deadlineDays: 9,
        ktru: "32.50.50.190-00001458", verdict: "disqualified", score: 38,
        checks: [
          { req: "материал = латекс", status: "fail", note: "в карточке нитрил — жёсткое несоответствие" },
          { req: "опудренность = да", status: "fail", note: "в карточке товар неопудренный" },
          { req: "размеры: S, M, L", status: "pass" }
        ],
        explanation: "Дисквалификация: заказчику нужны латексные опудренные перчатки, а не нитриловые. Материал — обязательный параметр, замены не допускаются по ТЗ."
      },
      {
        id: "t4", productId: "prod_gloves",
        title: "Поставка перчаток нитриловых смотровых (позиция №2 из 3 в лоте расходников)",
        number: "0844200001126000230", customer: "БУЗ УР «Республиканская клиническая больница»",
        source: "ЕИС", region: "Удмуртская Республика", price: 2260000, deadlineDays: 21,
        ktru: "32.50.50.190-00001458", verdict: "eligible", score: 97,
        lot: { position: 2, total: 3, others: ["Маски медицинские трёхслойные", "Шапочки одноразовые"] },
        checks: [
          { req: "материал = нитрил", status: "pass" },
          { req: "стерильность = нет", status: "pass" },
          { req: "толщина в области пальцев ≥ 0,11 мм", status: "pass" }
        ],
        explanation: "Сборный лот из 3 позиций — ваш товар закрывает позицию №2. Подаётесь на весь лот, но проходите именно по нужной вам позиции."
      },
      {
        id: "t5", productId: "prod_gloves",
        title: "Поставка перчаток нитриловых текстурированных на пальцах",
        number: "0326200015826000019", customer: "ГАУЗ «Краевая клиническая больница №1»",
        source: "РТС-тендер", region: "Краснодарский край", price: 980000, deadlineDays: 27,
        ktru: "32.50.50.190-00001458", verdict: "eligible_with_gaps", score: 84,
        checks: [
          { req: "материал = нитрил", status: "pass" },
          { req: "текстура на пальцах = да", status: "gap", note: "в карточке не указано — уточните у производителя" },
          { req: "длина манжеты ≥ 240 мм", status: "pass" }
        ],
        explanation: "Похоже, подходите. Не хватает данных по текстуре пальцев в карточке — это частое требование, стоит внести один раз и закрыть пробел для будущих закупок."
      },
      {
        id: "t6", productId: "prod_blade",
        title: "Поставка клинков ларингоскопических для анестезиологии-реанимации",
        number: "0351200000726000985", customer: "ГБУЗ «Областная клиническая больница»",
        source: "ЕИС", region: "Свердловская область", price: 560000, deadlineDays: 11,
        ktru: "32.50.13.190-00007686", verdict: "eligible", score: 89,
        checks: [
          { req: "совместимость = ISO 7376", status: "pass", note: "ТЗ: «Flexline» — семантически то же крепление" },
          { req: "форма = прямая", status: "pass" },
          { req: "тип освещения = волоконная оптика", status: "pass" },
          { req: "многоразовый, автоклавируемый", status: "pass" }
        ],
        explanation: "Проходите: ТЗ использует термин «Flexline», в карточке — ISO 7376, это одно и то же крепление. Форма и оптика совпадают полностью."
      },
      {
        id: "t7", productId: "prod_blade",
        title: "Поставка клинков к ларингоскопу, набор прямых и изогнутых",
        number: "0128200000926000341", customer: "ГБУЗ «Детская краевая клиническая больница»",
        source: "Росэлторг", region: "Приморский край", price: 310000, deadlineDays: 18,
        ktru: "32.50.13.190-00007686", verdict: "eligible_with_gaps", score: 71,
        checks: [
          { req: "форма = прямая, есть в наборе", status: "pass" },
          { req: "форма = изогнутая, есть в наборе", status: "gap", note: "в карточке только прямой клинок" },
          { req: "тип освещения = волоконная оптика", status: "pass" }
        ],
        explanation: "Заказчик просит набор из прямого и изогнутого клинков. У вас в карточке только прямой — по прямому пройдёте, по набору в целом будет пробел."
      },
      {
        id: "t8", productId: "prod_blade",
        title: "Поставка ларингоскопов с одноразовыми клинками (полный комплект)",
        number: "0166200002326000104", customer: "ГБУЗ «Городская больница скорой помощи»",
        source: "ЕИС", region: "Ростовская область", price: 1120000, deadlineDays: 8,
        ktru: "32.50.13.190-00007686", verdict: "disqualified", score: 22,
        checks: [
          { req: "одноразовость = да, обязательна", status: "fail", note: "в карточке многоразовый клинок" },
          { req: "комплект с рукоятью", status: "gap" }
        ],
        explanation: "Дисквалификация: заказчику нужны одноразовые клинки без права повторной стерилизации — жёсткое требование, многоразовый вариант не подойдёт."
      },
      {
        id: "t9", productId: "prod_gloves",
        title: "Поставка перчаток нитриловых для лабораторий контроля качества",
        number: "0872300007826000067", customer: "ФБУЗ «Центр гигиены и эпидемиологии»",
        source: "РТС-маркет", region: "Новосибирская область", price: 275000, deadlineDays: 4,
        ktru: "32.50.50.190-00001458", verdict: "eligible", score: 100,
        checks: [
          { req: "материал = нитрил", status: "pass" },
          { req: "химстойкость к спиртам", status: "pass" },
          { req: "размеры: S, M, L", status: "pass" }
        ],
        explanation: "Полное совпадение по всем пунктам ТЗ. Срок подачи короткий — успевайте за 4 дня."
      },
      {
        id: "t10", productId: "prod_blade",
        title: "Поставка изделий медицинского назначения для оториноларингологии",
        number: "0356200000426000512", customer: "ГБУЗ «Клиническая больница №4»",
        source: "Сбербанк-АСТ", region: "Воронежская область", price: 890000, deadlineDays: 23,
        ktru: "32.50.13.190-00007686", verdict: "eligible_with_gaps", score: 76,
        checks: [
          { req: "совместимость = ISO 7376", status: "pass" },
          { req: "форма = прямая", status: "pass" },
          { req: "срок службы ≥ 5 лет, документально", status: "gap", note: "нет протокола испытаний в карточке" }
        ],
        explanation: "Технически подходите. Заказчик просит документальное подтверждение срока службы — добавьте протокол в карточку, чтобы закрыть пробел."
      }
    ];
  }

  return {
    getCompany, setCompany, clearSession, isLoggedIn,
    getProducts, setProducts, addProduct, getCurrentProduct, setCurrentProduct,
    seedDemo, allTenders
  };
})();

// ---------- shared small utils ----------

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

// ---------- header auth-state (index.html) ----------

function lkInitHeaderState() {
  const cta = document.querySelector("[data-header-cta]");
  if (!cta) return;
  if (LK.isLoggedIn()) {
    cta.innerHTML = `<a class="btn btn-ghost btn-sm" href="app.html">В приложение →</a>`;
    const loginLink = document.querySelector("[data-header-login]");
    if (loginLink) loginLink.style.display = "none";
  }
}
