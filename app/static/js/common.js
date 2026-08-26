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
