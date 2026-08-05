let digestData = null;
let marketData = null;

async function loadDigest() {
  try {
    const res = await fetch('data/digest.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('no digest yet');
    digestData = await res.json();
  } catch (err) {
    digestData = { items: [] };
  }
  renderDigest();
}

async function loadMarket() {
  try {
    const res = await fetch('data/market.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('no market data yet');
    marketData = await res.json();
  } catch (err) {
    marketData = null;
  }
  renderMarket();
}

function renderDigest() {
  const items = digestData.items || [];
  const domestic = items.filter(i => i.category === 'domestic');
  const global = items.filter(i => i.category === 'global');
  const personal = items.filter(i => i.category === 'personal');

  renderList('list-domestic', domestic);
  renderList('list-global', global);
  renderList('list-personal', personal);
  renderList('homeHighlights', domestic.concat(global).slice(0, 3));

  document.getElementById('emptyStateGeneral').hidden = domestic.length > 0 || global.length > 0;
  document.getElementById('emptyStatePersonal').hidden = personal.length > 0;

  const lastUpdated = document.getElementById('lastUpdated');
  if (digestData.generated_at) {
    const d = new Date(digestData.generated_at);
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

function fmtNumber(n, decimals = 2) {
  if (typeof n !== 'number') return '—';
  return n.toLocaleString('id-ID', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtChange(pct) {
  if (typeof pct !== 'number') return '';
  const sign = pct > 0 ? '+' : '';
  return `${sign}${fmtNumber(pct)}%`;
}

function changeClass(pct) {
  if (typeof pct !== 'number') return '';
  return pct > 0 ? 'is-up' : pct < 0 ? 'is-down' : '';
}

function statCard({ label, value, change, sub }) {
  const card = document.createElement('div');
  card.className = 'stat-card';
  card.innerHTML = `
    <span class="stat-card__label">${label}</span>
    <span class="stat-card__value">${value}</span>
    ${change !== undefined ? `<span class="stat-card__change ${changeClass(change)}">${fmtChange(change)}</span>` : ''}
    ${sub ? `<span class="stat-card__sub">${sub}</span>` : ''}
  `;
  return card;
}

function renderMarket() {
  const statsEl = document.getElementById('marketStats');
  const stripEl = document.getElementById('homeMarketStrip');
  const updatedEl = document.getElementById('marketUpdated');
  statsEl.innerHTML = '';
  stripEl.innerHTML = '';

  if (!marketData) {
    statsEl.innerHTML = '<p class="empty-state">Data pasar belum tersedia.</p>';
    return;
  }

  const { ihsg, gold_world, gold_antam } = marketData;

  if (ihsg) {
    statsEl.appendChild(statCard({
      label: '📊 IHSG',
      value: fmtNumber(ihsg.value),
      change: ihsg.change_percent,
      sub: `Sebelumnya: ${fmtNumber(ihsg.previous_close)}`,
    }));
    stripEl.appendChild(statCard({ label: '📊 IHSG', value: fmtNumber(ihsg.value, 0), change: ihsg.change_percent }));
  }
  if (gold_world) {
    statsEl.appendChild(statCard({
      label: '🌍 Emas Dunia (USD/oz)',
      value: '$' + fmtNumber(gold_world.value),
      change: gold_world.change_percent,
    }));
    stripEl.appendChild(statCard({ label: '🌍 Emas Dunia', value: '$' + fmtNumber(gold_world.value, 0), change: gold_world.change_percent }));
  }
  if (gold_antam) {
    statsEl.appendChild(statCard({
      label: '🥇 Emas Antam (per gram)',
      value: 'Rp' + fmtNumber(gold_antam.buy, 0),
      change: gold_antam.change_percent,
      sub: gold_antam.buyback ? `Buyback: Rp${fmtNumber(gold_antam.buyback, 0)}` : undefined,
    }));
    stripEl.appendChild(statCard({ label: '🥇 Emas Antam', value: 'Rp' + fmtNumber(gold_antam.buy, 0), change: gold_antam.change_percent }));
  }

  if (!ihsg && !gold_world && !gold_antam) {
    statsEl.innerHTML = '<p class="empty-state">Data pasar belum tersedia.</p>';
  }

  if (marketData.generated_at) {
    const d = new Date(marketData.generated_at);
    updatedEl.textContent = 'Update ' + d.toLocaleString('id-ID', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
    });
  }

  renderMoodBanner(ihsg);
}

function renderMoodBanner(ihsg) {
  const banner = document.getElementById('moodBanner');
  if (!ihsg || typeof ihsg.change_percent !== 'number') {
    banner.hidden = true;
    return;
  }
  const pct = ihsg.change_percent;
  let emoji, text;
  if (pct >= 2) {
    emoji = '🚀'; text = `IHSG lagi ngegas banget, +${fmtNumber(pct)}% hari ini!`;
  } else if (pct >= 0.3) {
    emoji = '📈'; text = `IHSG hijau, naik +${fmtNumber(pct)}%. Lumayan nih.`;
  } else if (pct > -0.3) {
    emoji = '😌'; text = `IHSG adem ayem, cuma ${fmtNumber(pct)}%. Santai dulu.`;
  } else if (pct > -2) {
    emoji = '📉'; text = `IHSG lagi melempem, ${fmtNumber(pct)}%. Sabar ya.`;
  } else {
    emoji = '😬'; text = `IHSG merah menyala, ${fmtNumber(pct)}%. Tarik napas dulu.`;
  }
  document.getElementById('moodEmoji').textContent = emoji;
  document.getElementById('moodText').textContent = text;
  banner.hidden = false;
}

function setGreeting() {
  const el = document.getElementById('greetingKicker');
  if (!el) return;
  const hour = new Date().getHours();
  let greeting;
  if (hour < 4) greeting = '🌙 Begadang nih?';
  else if (hour < 10) greeting = '☕ Pagi!';
  else if (hour < 15) greeting = '🍜 Siang!';
  else if (hour < 18) greeting = '🌇 Sore!';
  else greeting = '🌃 Malam!';
  el.textContent = greeting;
}

function switchView(viewName) {
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('is-active', v.dataset.view === viewName));
  document.querySelectorAll('.drawer__link').forEach(l => l.classList.toggle('is-active', l.dataset.goto === viewName));
  window.scrollTo({ top: 0 });
}

function setupDrawer() {
  const drawer = document.getElementById('drawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const menuToggle = document.getElementById('menuToggle');
  const closeBtn = document.getElementById('drawerClose');

  function openDrawer() {
    drawer.classList.add('is-open');
    backdrop.classList.add('is-open');
    menuToggle.setAttribute('aria-expanded', 'true');
  }
  function closeDrawer() {
    drawer.classList.remove('is-open');
    backdrop.classList.remove('is-open');
    menuToggle.setAttribute('aria-expanded', 'false');
  }

  menuToggle.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  backdrop.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDrawer();
  });

  drawer.querySelectorAll('.drawer__link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      switchView(link.dataset.goto);
      closeDrawer();
    });
  });
}

function setupGotoButtons() {
  document.querySelectorAll('[data-goto]:not(.drawer__link)').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.goto));
    if (btn.getAttribute('role') === 'button') {
      btn.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          switchView(btn.dataset.goto);
        }
      });
    }
  });
}

function setupGeneralSubtabs() {
  const subtabs = document.querySelectorAll('.tabs--sub .subtab');
  subtabs.forEach(subtab => {
    subtab.addEventListener('click', () => {
      subtabs.forEach(t => {
        t.classList.remove('is-active');
        t.setAttribute('aria-selected', 'false');
      });
      subtab.classList.add('is-active');
      subtab.setAttribute('aria-selected', 'true');

      document.querySelectorAll('[data-subpanel]').forEach(p => {
        p.hidden = p.dataset.subpanel !== subtab.dataset.subtab;
      });
    });
  });
}

setupDrawer();
setupGotoButtons();
setupGeneralSubtabs();
setGreeting();
loadDigest();
loadMarket();
