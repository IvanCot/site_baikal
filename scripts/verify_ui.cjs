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
    for (const width of [320, 390, 768]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Страница входа: ${width}px`);
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.locator('#id_username').fill(credentials.username);
    await page.locator('#id_password').fill(credentials.password);
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button', { name: 'Войти →', exact: true }).click()]);
    await page.screenshot({ path: path.join(output, 'dashboard-desktop.png'), fullPage: true });
    let routes = ['/', '/calendar/', '/bookings/', '/guests/', '/rooms/', '/payments/', '/history/', '/settings/', '/audit/', '/bookings/new/', '/guests/new/', '/rooms/new/', '/payments/new/', '/settings/users/new/', '/settings/password/', '/statistics/', '/statistics/?period=week', '/statistics/?period=month', '/statistics/?period=all'];
    for (const source of ['/bookings/', '/guests/', '/rooms/', '/payments/', '/settings/']) {
      await page.goto(base + source);
      const links = await page.locator('main a[href]').evaluateAll(elements => elements.map(element => element.getAttribute('href')));
      const details = links.find(href => /^\/(bookings|guests)\/\d+\/$/.test(href));
      const edit = links.find(href => /^\/(bookings|guests|rooms|payments)\/\d+\/edit\/$/.test(href) || /^\/settings\/users\/\d+\/$/.test(href));
      if (details) {
        routes.push(details);
        await page.goto(base + details);
        const editLink = page.locator('main a[href$="/edit/"]').first();
        if (await editLink.count()) routes.push(await editLink.getAttribute('href'));
      }
      if (edit) routes.push(edit);
    }
    routes = [...new Set(routes)];
    for (const route of routes) {
      const response = await page.goto(base + route);
      assert.equal(response.status(), 200, route);
      assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
      assert.ok((await page.title()).includes('У батюшки'));
      assert.equal(await page.locator('.lake-art').count(), 0);
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
    assert.equal(await page.locator('#id_companions, #id_linen_sets, #id_documents, #id_total_cost, [id^="id_passport_"]').count(), 0);
    const firstRoom = await page.locator('#id_room option').evaluateAll(options => options.find(option => option.value)?.value);
    await page.locator('#id_room').selectOption(firstRoom);
    await page.locator('#id_check_in').fill('2026-11-10T14:00');
    await page.locator('#id_check_out').fill('2026-11-13T12:00');
    assert.equal(await page.locator('#booking-days').textContent(), '3');
    const pricing = await page.locator('#booking-pricing').textContent();
    const rate = Number(JSON.parse(pricing).rates[firstRoom]);
    const displayed = await page.locator('#booking-total').textContent();
    assert.equal(Number(displayed.replace(/[^\d,]/g, '').replace(',', '.')), rate * 3);
    await page.locator('#id_prepayment').fill('100');
    await page.locator('#id_payment_method').selectOption('other');
    assert.equal(await page.locator('#id_payment_comment').evaluate(element => element.required), true);
    await page.screenshot({ path: path.join(output, 'booking-form-desktop.png'), fullPage: true });
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.fonts.check('600 13px "Noto Sans"', 'ФИО')), true, 'Кириллический шрифт загружен');
    const fontResponse = await context.request.get(base + '/static/fonts/noto-sans.woff2');
    assert.equal(fontResponse.status(), 200);
    for (const [width, height] of [[1440, 900], [1280, 600], [1024, 480], [900, 600], [768, 360], [390, 844], [320, 568]]) {
      await page.setViewportSize({ width, height });
      for (const route of routes) {
        const response = await page.goto(base + route);
        assert.equal(response.status(), 200, route);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `Переполнение ${width}px: ${route}`);
      }
      await page.goto(base + '/');
      if (width <= 900) {
        assert.equal(await page.locator('#sidebar').evaluate(element => element.inert), true);
        await page.locator('#menu-toggle').click();
        await page.waitForFunction(() => Math.abs(document.querySelector('#sidebar').getBoundingClientRect().left) < 0.5);
        assert.equal(await page.locator('#sidebar').evaluate(element => element.inert), false);
      }
      const layout = await page.evaluate(() => {
        const nav = document.querySelector('.sidebar nav');
        nav.scrollTop = nav.scrollHeight;
        const last = nav.lastElementChild.getBoundingClientRect();
        return {
          logoutBottom: document.querySelector('.logout').getBoundingClientRect().bottom,
          lastTop: last.top, lastBottom: last.bottom,
          navTop: nav.getBoundingClientRect().top, navBottom: nav.getBoundingClientRect().bottom,
        };
      });
      assert.ok(layout.logoutBottom <= height, `Выход за пределами экрана ${width}×${height}`);
      assert.ok(layout.lastTop >= layout.navTop - 1 && layout.lastBottom <= layout.navBottom + 1, `Настройки недоступны в меню ${width}×${height}`);
      if (width === 1280) await page.screenshot({ path: path.join(output, 'dashboard-short-desktop.png'), fullPage: false });
      if (width <= 900) {
        if (width === 390) await page.screenshot({ path: path.join(output, 'menu-mobile.png'), fullPage: false });
        if (width === 768) await page.screenshot({ path: path.join(output, 'menu-landscape.png'), fullPage: false });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'), 'false');
        assert.equal(await page.locator('#menu-toggle').evaluate(element => element === document.activeElement), true);
        await page.locator('#menu-toggle').click();
        await page.locator('#sidebar-backdrop').click({ position: { x: width - 5, y: 10 } });
        assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'), 'false');
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base + '/');
    await page.locator('#menu-toggle').click();
    assert.equal(await page.locator('#menu-toggle').getAttribute('aria-expanded'), 'true');
    await page.getByRole('navigation').getByRole('link', { name: 'Календарь' }).click();
    await page.waitForURL(base + '/calendar/');
    await page.goto(base + '/');
    await page.screenshot({ path: path.join(output, 'dashboard-mobile.png'), fullPage: true });
    await page.goto(base + '/guests/');
    await page.screenshot({ path: path.join(output, 'guests-mobile.png'), fullPage: true });
    await page.goto(base + '/statistics/?period=week');
    await page.screenshot({ path: path.join(output, 'statistics-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 600 });
    await page.goto(base + '/guests/new/');
    await page.screenshot({ path: path.join(output, 'guest-form-desktop.png'), fullPage: false });
    await page.goto(base + '/statistics/?period=month');
    await page.screenshot({ path: path.join(output, 'statistics-desktop.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(`Интерфейс проверен: ${routes.length} страниц на 7 размерах экрана (320–1440px), короткое и мобильное меню, шрифт, календарь и поиск гостя; ошибок JavaScript нет.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
