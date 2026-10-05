document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  const close = document.getElementById('menu-close');
  const drawer = window.matchMedia('(max-width: 900px)');
  const setMenu = (open, returnFocus = false) => {
    if (!sidebar) return;
    const expanded = drawer.matches && open;
    sidebar.classList.toggle('open', expanded);
    sidebar.inert = drawer.matches && !expanded;
    backdrop.hidden = !expanded;
    document.body.classList.toggle('menu-open', expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Закрыть меню' : 'Открыть меню');
    if (expanded) close.focus();
    else if (returnFocus) toggle.focus();
  };
  toggle?.addEventListener('click', () => setMenu(!sidebar.classList.contains('open')));
  close?.addEventListener('click', () => setMenu(false, true));
  backdrop?.addEventListener('click', () => setMenu(false, true));
  drawer.addEventListener('change', () => setMenu(false));
  setMenu(false);
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
