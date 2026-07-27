// Проба: пройдёт ли headless-браузер антибот RTS-маркета.
// node tools/scrapers/rts_probe.js
const { chromium } = require("playwright");

const SP = "C:/Users/nikit/AppData/Local/Temp/claude/C--Users-nikit-OneDrive-Dokumente-TenderPlan-COPY/2deac842-ae68-4266-9da9-2213f1d3049c/scratchpad";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    locale: "ru-RU",
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();
  const targets = [
    "https://www.rts-market.ru/",
    "https://www.rts-market.ru/Trade/Search",
  ];
  for (const url of targets) {
    try {
      const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
      await page.waitForTimeout(3500);
      const title = await page.title();
      const bodyLen = (await page.content()).length;
      const text = (await page.evaluate(() => document.body ? document.body.innerText : "")).slice(0, 300);
      const challenge = /checking your browser|ddos-guard|проверка браузера|cloudflare|attention required/i.test(text + title);
      const links = await page.evaluate(() =>
        [...document.querySelectorAll("a[href]")].map(a => a.getAttribute("href"))
          .filter(h => h && /trade|procedure|purchase|lot|torg|search/i.test(h)).slice(0, 8));
      console.log(`\n=== ${url} ===`);
      console.log(`status=${resp && resp.status()} title="${title}" htmlLen=${bodyLen} challenge=${challenge}`);
      console.log(`text[0..300]: ${text.replace(/\s+/g, " ")}`);
      console.log(`links: ${JSON.stringify(links)}`);
      await page.screenshot({ path: `${SP}/rts_${url.includes("Search") ? "search" : "home"}.png`, fullPage: false });
    } catch (e) {
      console.log(`\n=== ${url} ===\nОШИБКА: ${e.message}`);
    }
  }
  await browser.close();
})();
