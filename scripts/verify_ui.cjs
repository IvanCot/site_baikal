// Проверка интерфейса; данные входа передаются отдельным локальным файлом.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const credentials = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8000';
  const output = path.resolve(process.env.UI_OUTPUT_DIR || 'test-results');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'ru-RU' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/login/');
    await page.locator('#id_username').fill(credentials.username);
    await page.locator('#id_password').fill(credentials.password);
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button', { name: 'Войти →', exact: true }).click()]);
    await page.screenshot({ path: path.join(output, 'dashboard-desktop.png'), fullPage: true });
    const routes = ['/', '/calendar/', '/bookings/', '/guests/', '/rooms/', '/payments/', '/history/', '/settings/', '/audit/', '/bookings/new/'];
    for (const route of routes) {
      const response = await page.goto(base + route);
      assert.equal(response.status(), 200, route);
      assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      assert.equal(overflow, false, `Горизонтальное переполнение: ${route}`);
    }
    await page.goto(base + '/calendar/');
    const free = page.locator('a.calendar-free').first();
    if (await free.count()) {
      const href = await free.getAttribute('href');
      const url = new URL(href, base);
      await free.click();
      assert.equal(await page.locator('#id_room').inputValue(), url.searchParams.get('room'));
      assert.ok((await page.locator('#id_check_in').inputValue()).startsWith(url.searchParams.get('date')));
    }
    await page.goto(base + '/calendar/');
    await page.screenshot({ path: path.join(output, 'calendar-desktop.png'), fullPage: true });
    await page.goto(base + '/bookings/new/');
    await page.locator('#guest-search').fill('Иванов');
    await page.locator('#guest-results button').first().waitFor();
    await page.locator('#guest-results button').first().click();
    assert.ok(await page.locator('#id_primary_guest').inputValue());
    await page.screenshot({ path: path.join(output, 'booking-form-desktop.png'), fullPage: true });
    for (const width of [768, 390]) {
      await page.setViewportSize({ width, height: 844 });
      for (const route of routes) {
        const response = await page.goto(base + route);
        assert.equal(response.status(), 200, route);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Переполнение ${width}px: ${route}`);
      }
    }
    await page.goto(base + '/');
    await page.locator('#menu-toggle').click();
    assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'), 'true');
    await page.getByRole('navigation').getByRole('link', { name: 'Календарь' }).click();
    await page.waitForURL(base + '/calendar/');
    await page.goto(base + '/');
    await page.screenshot({ path: path.join(output, 'dashboard-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('Интерфейс проверен: 10 страниц, 1440px, 768px и 390px, календарь, поиск гостя, мобильное меню; ошибок JavaScript нет.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
