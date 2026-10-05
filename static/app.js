document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  toggle?.addEventListener('click', () => {
    const open = sidebar.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  });
  document.addEventListener('click', event => {
    if (sidebar?.classList.contains('open') && !sidebar.contains(event.target) && !toggle.contains(event.target)) {
      sidebar.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
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
