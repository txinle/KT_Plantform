(() => {
  'use strict';

  const search = document.querySelector('[data-advanced-table-search]');
  if (!search) return;

  const groups = Array.from(document.querySelectorAll('[data-advanced-group]'));
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase('zh-CN');
    groups.forEach((group) => {
      const links = Array.from(group.querySelectorAll('[data-advanced-table-link]'));
      let visible = 0;
      links.forEach((link) => {
        const matches = !query || (link.dataset.searchText || '').toLocaleLowerCase('zh-CN').includes(query);
        link.hidden = !matches;
        if (matches) visible += 1;
      });
      group.hidden = Boolean(query) && visible === 0;
      if (query && visible > 0) group.open = true;
    });
  });
})();
