try { if (localStorage.getItem('sv-theme') === 'dark') document.body.classList.add('dark'); } catch {}
for (const link of document.querySelectorAll('aside a')) if (new URL(link.href).pathname === location.pathname) link.setAttribute('aria-current','page');
const search = document.getElementById('help-search');
if (search) search.oninput = () => {
  const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let found = 0;
  for (const card of document.querySelectorAll('[data-guide]')) {
    card.hidden = !terms.every(term => card.dataset.guide.toLowerCase().includes(term));
    if (!card.hidden) found++;
  }
  document.getElementById('help-results').textContent = found+' guides found';
  document.getElementById('help-empty').hidden = found > 0;
};
