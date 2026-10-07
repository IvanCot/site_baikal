document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  const close = document.getElementById('menu-close');
  const drawer = window.matchMedia('(max-width: 900px)');
  const scrollActiveMenu = () => {
    const nav = sidebar?.querySelector('nav');
    const active = nav?.querySelector('a.active');
    if (active) nav.scrollTop += active.getBoundingClientRect().top - nav.getBoundingClientRect().top - (nav.clientHeight - active.clientHeight) / 2;
  };
  const setMenu = (open, returnFocus = false) => {
    if (!sidebar) return;
    const expanded = drawer.matches && open;
    sidebar.classList.toggle('open', expanded);
    sidebar.inert = drawer.matches && !expanded;
    backdrop.hidden = !expanded;
    document.body.classList.toggle('menu-open', expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Закрыть меню' : 'Открыть меню');
    if (expanded) { scrollActiveMenu(); close.focus(); }
    else if (returnFocus) toggle.focus();
  };
  toggle?.addEventListener('click', () => setMenu(!sidebar.classList.contains('open')));
  close?.addEventListener('click', () => setMenu(false, true));
  backdrop?.addEventListener('click', () => setMenu(false, true));
  drawer.addEventListener('change', () => setMenu(false));
  setMenu(false);
  scrollActiveMenu();
  const pricingElement = document.getElementById('booking-pricing');
  if (pricingElement) {
    const pricing = JSON.parse(pricingElement.textContent);
    const room = document.getElementById('id_room');
    const start = document.getElementById('id_check_in');
    const end = document.getElementById('id_check_out');
    const currency = new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB'});
    const cents = value => {
      const [whole, fraction = ''] = String(value).split('.');
      return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2));
    };
    const localDay = value => {
      const [year, month, day] = value.slice(0, 10).split('-').map(Number);
      return Date.UTC(year, month - 1, day);
    };
    const updatePrice = () => {
      const original = pricing.original;
      const unchanged = original && room.value === original.room && start.value === original.start && end.value === original.end;
      const rate = original && room.value === original.room && original.rate !== null ? original.rate : pricing.rates[room.value];
      const days = start.value && end.value && end.value > start.value ? Math.max(1, Math.round((localDay(end.value) - localDay(start.value)) / 86400000)) : null;
      document.getElementById('booking-rate').textContent = unchanged && original.rate === null ? 'Ранее согласованная сумма' : rate !== undefined ? currency.format(Number(rate)) : '—';
      document.getElementById('booking-days').textContent = days ?? '—';
      const total = unchanged ? cents(original.total) : days && rate !== undefined ? cents(rate) * BigInt(days) : null;
      document.getElementById('booking-total').textContent = total === null ? '—' : currency.format(Number(total) / 100);
    };
    [room, start, end].forEach(input => input.addEventListener('input', updatePrice));
    updatePrice();
  }
  const method = document.getElementById('id_method') || document.getElementById('id_payment_method');
  const paymentComment = document.getElementById('id_payment_comment') || (method && document.getElementById('id_comment'));
  const prepayment = document.getElementById('id_prepayment');
  if (method && paymentComment) {
    const updateRequired = () => {
      paymentComment.required = method.value === 'other' && (!prepayment || Number(prepayment.value) > 0);
      paymentComment.placeholder = method.value === 'other' ? 'Укажите, как была выполнена оплата' : 'Необязательный комментарий';
    };
    method.addEventListener('change', updateRequired);
    prepayment?.addEventListener('input', updateRequired);
    updateRequired();
  }
  document.addEventListener('keydown', event => {
    if (!sidebar?.classList.contains('open')) return;
    if (event.key === 'Escape') setMenu(false, true);
    if (event.key === 'Tab') {
      const focusable = [...sidebar.querySelectorAll('a[href], button')];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll('.table-wrap table').forEach(table => {
    const labels = [...table.querySelectorAll('thead th')].map(header => header.textContent.trim());
    table.querySelectorAll('tbody tr').forEach(row => {
      if (row.cells.length !== labels.length) return;
      [...row.cells].forEach((cell, index) => {
        cell.dataset.label = labels[index];
        const value = document.createElement('div');
        value.className = 'cell-value';
        while (cell.firstChild) value.append(cell.firstChild);
        cell.append(value);
      });
    });
  });
  const search = document.getElementById('guest-search');
  const results = document.getElementById('guest-results');
  let timer;
  let controller;
  search?.addEventListener('input', () => {
    clearTimeout(timer);
    controller?.abort();
    results.replaceChildren();
    const query = search.value.trim();
    if (!query) return;
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`${search.dataset.searchUrl}?q=${encodeURIComponent(query)}`, {signal: controller.signal});
        if (!response.ok) throw new Error();
        const data = await response.json();
        results.replaceChildren();
        if (!data.results.length) {
          results.textContent = 'Гость не найден. Заполните ФИО и телефон нового гостя ниже.';
        }
        data.results.forEach(guest => {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = guest.text;
          button.addEventListener('click', () => {
            const select = document.getElementById('id_primary_guest');
            if (![...select.options].some(option => option.value === String(guest.id))) select.add(new Option(guest.text, guest.id));
            select.value = String(guest.id);
            select.dispatchEvent(new Event('change'));
            document.getElementById('id_new_guest_name').value = '';
            document.getElementById('id_new_guest_phone').value = '';
            search.value = guest.text;
            results.replaceChildren();
          });
          results.append(button);
        });
      } catch (error) {
        if (error.name !== 'AbortError') results.textContent = 'Не удалось выполнить поиск. Выберите гостя из списка ниже.';
      }
    }, 250);
  });
});
