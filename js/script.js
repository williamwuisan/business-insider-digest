async function loadDigest() {
  const emptyState = document.getElementById('emptyState');
  try {
    const res = await fetch('data/digest.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('no digest yet');
    const data = await res.json();
    render(data);
  } catch (err) {
    emptyState.hidden = false;
    document.getElementById('lastUpdated').textContent = 'Belum ada data';
  }
}

function render(data) {
  const domestic = (data.items || []).filter(i => i.category === 'domestic');
  const global = (data.items || []).filter(i => i.category === 'global');

  renderList('list-domestic', domestic);
  renderList('list-global', global);

  const emptyState = document.getElementById('emptyState');
  emptyState.hidden = domestic.length > 0 || global.length > 0;

  const lastUpdated = document.getElementById('lastUpdated');
  if (data.generated_at) {
    const d = new Date(data.generated_at);
    lastUpdated.textContent = 'Update ' + d.toLocaleString('id-ID', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
    });
  }
}

function renderList(elId, items) {
  const el = document.getElementById(elId);
  el.innerHTML = '';
  items.forEach(item => {
    const card = document.createElement('article');
    card.className = 'card';

    const topic = document.createElement('span');
    topic.className = 'card__topic';
    topic.textContent = item.tag || 'Berita';
    card.appendChild(topic);

    const title = document.createElement('h3');
    title.className = 'card__title';
    title.textContent = item.title;
    card.appendChild(title);

    const summary = document.createElement('p');
    summary.className = 'card__summary';
    summary.textContent = item.summary;
    card.appendChild(summary);

    const sources = document.createElement('div');
    sources.className = 'card__sources';
    (item.sources || []).forEach(src => {
      const a = document.createElement('a');
      a.className = 'source-chip';
      a.href = src.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = src.name;
      sources.appendChild(a);
    });
    card.appendChild(sources);

    el.appendChild(card);
  });
}

function setupTabs() {
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => {
        t.classList.remove('is-active');
        t.setAttribute('aria-selected', 'false');
      });
      tab.classList.add('is-active');
      tab.setAttribute('aria-selected', 'true');

      document.querySelectorAll('.panel').forEach(p => p.classList.remove('is-active'));
      document.getElementById('panel-' + tab.dataset.tab).classList.add('is-active');
    });
  });
}

setupTabs();
loadDigest();
