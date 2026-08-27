// Shared across all pages: connection status badge polling

async function refreshConnBadge() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const badge = document.getElementById('conn-badge');
    const text = document.getElementById('conn-text');
    if (!badge || !text) return;

    if (data.connected) {
      badge.className = 'conn-badge conn-ok';
      text.textContent = data.simulate_mode ? 'Connected (Demo Mode)' : 'Connected';
    } else {
      badge.className = 'conn-badge conn-bad';
      text.textContent = data.last_error ? 'Disconnected' : 'Connecting...';
    }
  } catch (e) {
    const badge = document.getElementById('conn-badge');
    if (badge) {
      badge.className = 'conn-badge conn-bad';
      document.getElementById('conn-text').textContent = 'Server unreachable';
    }
  }
}

refreshConnBadge();
setInterval(refreshConnBadge, 4000);

function fmt(val, decimals = 1) {
  if (val === null || val === undefined) return '--';
  return Number(val).toFixed(decimals);
}

function timeAgo(isoString) {
  if (!isoString) return '';
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ---------- Theme: system / dark / light, persisted in localStorage ----------

const THEME_STORAGE_KEY = 'pumpguru-theme';

function resolveTheme(pref) {
  if (pref === 'system') {
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
  }
  return pref;
}

function applyTheme(pref) {
  const resolved = resolveTheme(pref);
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-pref', pref);
  document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.themeChoice === pref);
  });
}

function setThemePref(pref) {
  try { localStorage.setItem(THEME_STORAGE_KEY, pref); } catch (e) { /* storage unavailable */ }
  applyTheme(pref);
}

function initThemeToggle() {
  let stored = 'system';
  try { stored = localStorage.getItem(THEME_STORAGE_KEY) || 'system'; } catch (e) { /* ignore */ }

  // base.html's inline head script already set data-theme before paint;
  // this just syncs the toggle button states and wires up clicks.
  applyTheme(stored);

  document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => setThemePref(btn.dataset.themeChoice));
  });

  // Live-follow OS theme changes only while "system" is selected
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      const current = document.documentElement.getAttribute('data-theme-pref');
      if (current === 'system') applyTheme('system');
    });
  }
}

initThemeToggle();