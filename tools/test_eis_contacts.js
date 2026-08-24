// Тест извлечения контактов заказчика из карточки ЕИС (eis.js extractContacts).
// Сети не трогает — фикстуры повторяют реальную вёрстку обеих структур:
//   223-ФЗ — common-text__title / common-text__value (div);
//   44-ФЗ  — section__title / section__info (span).
// Обе сняты с живых страниц zakupki.gov.ru при разработке пункта 6.

const { test } = require("node:test");
const assert = require("node:assert");
const { extractContacts } = require("./sources/eis");

const HTML_223 = `
<div class="col-9 mr-auto"><div class="common-text__title">Контактное лицо</div>
  <div class="common-text__value"> Морозова Ю.С. </div></div>
<div class="col-9 mr-auto"><div class="common-text__title">Адрес электронной почты</div>
  <div class="common-text__value"><a href="mailto:Morozova-YuS@rosseti-ural.ru">Morozova-YuS@rosseti-ural.ru</a></div></div>
<div class="col-9 mr-auto"><div class="common-text__title">Номер контактного телефона</div>
  <div class="common-text__value"> +7 (343) 2932583 </div></div>`;

const HTML_44 = `
<span class="section__title">Ответственное должностное лицо</span><span class="section__info"> Долгова И. В. </span>
<span class="section__title">Адрес электронной почты</span><span class="section__info"> umz@norilsk-city.ru </span>
<span class="section__title">Номер контактного телефона</span><span class="section__info"> 8-3919-437010 </span>`;

test("223-ФЗ: common-text структура", () => {
  const c = extractContacts(HTML_223);
  assert.strictEqual(c.person, "Морозова Ю.С.");
  assert.strictEqual(c.email, "Morozova-YuS@rosseti-ural.ru");
  assert.strictEqual(c.phone, "+7 (343) 2932583");
});

test("44-ФЗ: section структура и метка «Ответственное должностное лицо»", () => {
  const c = extractContacts(HTML_44);
  assert.strictEqual(c.person, "Долгова И. В.");
  assert.strictEqual(c.email, "umz@norilsk-city.ru");
  assert.strictEqual(c.phone, "8-3919-437010");
});

test("нет блока контактов → null (а не пустой объект)", () => {
  assert.strictEqual(extractContacts("<div>ничего про контакты</div>"), null);
});

test("частичные контакты: только телефон", () => {
  const html = `<div class="common-text__title">Номер контактного телефона</div>
    <div class="common-text__value">8 800 000</div>`;
  const c = extractContacts(html);
  assert.strictEqual(c.phone, "8 800 000");
  assert.ok(!("email" in c) && !("person" in c));
});
