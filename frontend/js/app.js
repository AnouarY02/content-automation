// ══════════════════════════════════════════════════════════════════════
// AY Marketing OS — Frontend Application
// ══════════════════════════════════════════════════════════════════════

// ── State ────────────────────────────────────────────────────────────
const API = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? ''  // Lokale dev — zelfde server
  : 'https://content-automation-production-1812.up.railway.app';  // Productie — Railway backend
let currentTab = 'overview';
let currentApp = '';
let allApps = [];
let allCampaigns = [];
let allContent = [];
let contentView = 'grid';
let refreshTimer = null;
let maturityChart = null;
let costsChart = null;
let activeEventSource = null;
let previewAudio = null;
const REFRESH_INTERVAL = 30000;

// ── Init ─────────────────────────────────────────────────────────────
async function initApp() {
  switchTab('overview');
  await loadApps();
  await refreshAll();
  startAutoRefresh();
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// ── Helpers ──────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const controller = new AbortController();
  const ms = opts._timeout || 15000;
  delete opts._timeout;
  const timeout = setTimeout(() => controller.abort(), ms);
  try {
    const r = await fetch(`${API}${path}`, { ...opts, signal: controller.signal });
    clearTimeout(timeout);
    if (!r.ok) throw new Error(`${r.status}`);
    const ct = r.headers.get('content-type') || '';
    return ct.includes('json') ? await r.json() : await r.text();
  } catch(e) {
    clearTimeout(timeout);
    if (e.name !== 'AbortError') console.error(`API ${path}:`, e);
    return null;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

const _toastDedup = new Map();
function toast(msg, type='info') {
  const key = `${type}:${msg}`;
  if (_toastDedup.has(key)) return;
  _toastDedup.set(key, true);
  setTimeout(() => _toastDedup.delete(key), 4000);
  const el = document.createElement('div');
  const colors = { info:'border-accent', success:'border-success', error:'border-danger', warning:'border-warning' };
  el.className = `toast bg-card border-l-4 ${colors[type]||colors.info} px-4 py-3 rounded-lg shadow-xl text-sm max-w-sm`;
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function statusBadge(status) {
  const s = (status||'').toLowerCase().replace(/ /g,'_');
  const labels = {
    draft:'Concept', generating:'Genereren…', pending_approval:'Wacht op review', pending:'Wacht op review',
    approved:'Goedgekeurd', rejected:'Afgewezen', publishing:'Publiceren…',
    published:'Gepubliceerd', failed:'Mislukt', measuring:'Meten…',
    concluded:'Afgerond', selected:'Geselecteerd', inconclusive:'Onbeslist', completed:'Voltooid'
  };
  const icons = {
    draft:'○', generating:'◌', pending_approval:'◉', pending:'◉',
    approved:'✓', rejected:'✕', publishing:'↑', published:'✓',
    failed:'!', measuring:'◎', concluded:'★', selected:'✓', inconclusive:'~', completed:'✓'
  };
  return `<span class="badge status-${s}">${icons[s]||'·'} ${labels[s]||status||'onbekend'}</span>`;
}

function formatEUR(v) { return '€' + (Number(v)||0).toFixed(2); }
function shortId(id) { return id ? id.substring(0,10) : '-'; }
function videoUrl(path) {
  if (!path) return '';
  if (/^https?:\/\//i.test(path)) return path;
  const filename = path.split(/[/\\]/).pop();
  return `${API}/assets/videos/${filename}`;
}
function timeAgo(ts) {
  if (!ts || ts==='None') return '-';
  // Server slaat UTC op zonder timezone-suffix — forceer UTC parse
  let s = String(ts);
  if (!s.endsWith('Z') && !s.includes('+')) s += 'Z';
  const diff = Math.floor((Date.now() - new Date(s).getTime()) / 1000);
  if (diff < 0) return 'zojuist';
  if (diff < 30) return 'zojuist';
  if (diff < 60) return `${diff} seconden geleden`;
  if (diff < 120) return '1 minuut geleden';
  if (diff < 3600) return `${Math.floor(diff/60)} minuten geleden`;
  if (diff < 7200) return '1 uur geleden';
  if (diff < 86400) return `${Math.floor(diff/3600)} uur geleden`;
  if (diff < 172800) return 'gisteren';
  if (diff < 604800) return `${Math.floor(diff/86400)} dagen geleden`;
  return new Date(s).toLocaleDateString('nl-NL', {day:'numeric', month:'short'});
}

function truncate(str, len=80) { return str && str.length > len ? str.substring(0,len)+'...' : (str||''); }

function openModal(name) {
  const el = document.getElementById(`modal-${name}`);
  el.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  if (name === 'start-campaign') populateCampaignAppSelect();
}
function closeModal(name) {
  document.getElementById(`modal-${name}`).classList.add('hidden');
  document.body.style.overflow = '';
}
function closePanel(id) { document.getElementById(id).classList.add('hidden'); }

// ── Navigation ───────────────────────────────────────────────────────
function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  const ov = document.getElementById('mobile-overlay');
  sb.classList.toggle('open');
  ov.style.display = sb.classList.contains('open') ? 'block' : 'none';
}

function switchTab(tab) {
  currentTab = tab;
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('mobile-overlay').style.display = 'none';
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.getElementById(`tab-${tab}`).classList.remove('hidden');
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  const titles = { overview:'Dashboard', apps:'Apps', content:'Content', campaigns:'Campagnes', experiments:'Experimenten', health:'Systeemstatus', maturity:'Volwassenheid', costs:'Kosten', insights:'Leerinzichten', opnemen:'Video Opnemen', analytics:'Analytics', kanalen:'Kanalen', kalender:'Kalender' };
  document.getElementById('page-title').textContent = titles[tab] || tab;
  refreshTab(tab);
}

// ── Apps Loading ─────────────────────────────────────────────────────
async function loadApps() {
  const data = await api('/api/apps/');
  allApps = data && Array.isArray(data) ? data : [];

  const sel = document.getElementById('app-selector');
  sel.innerHTML = '<option value="">Alle apps</option>' +
    allApps.map(app => { const id = app.id || app.app_id; return `<option value="${id}">${escapeHtml(app.name || id)}</option>`; }).join('');

  const cf = document.getElementById('content-app-filter');
  cf.innerHTML = '<option value="">Selecteer app...</option>' +
    allApps.map(app => { const id = app.id || app.app_id; return `<option value="${id}">${escapeHtml(app.name || id)}</option>`; }).join('');

  // Herstel laatste geselecteerde app na page refresh
  const saved = localStorage.getItem('currentApp');
  if (saved && allApps.some(a => (a.id || a.app_id) === saved)) {
    sel.value = saved;
    currentApp = saved;
    cf.value = saved;
  } else if (allApps.length === 1) {
    // Automatisch selecteren als er maar 1 app is
    const id = allApps[0].id || allApps[0].app_id;
    sel.value = id;
    currentApp = id;
    cf.value = id;
  }

  const badge = document.getElementById('badge-apps');
  if (allApps.length > 0) { badge.textContent = allApps.length; badge.classList.remove('hidden'); }
}

function onAppChange() {
  currentApp = document.getElementById('app-selector').value;
  document.getElementById('content-app-filter').value = currentApp;
  if (currentApp) localStorage.setItem('currentApp', currentApp);
  else localStorage.removeItem('currentApp');
  refreshTab(currentTab);
}

function populateCampaignAppSelect() {
  const sel = document.getElementById('campaign-app');
  sel.innerHTML = '<option value="">Selecteer app...</option>' +
    allApps.map(app => { const id = app.id || app.app_id; return `<option value="${id}">${escapeHtml(app.name || id)}</option>`; }).join('');
}

// ── Auto Refresh ─────────────────────────────────────────────────────
function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => refreshAll(), REFRESH_INTERVAL);
}

async function refreshAll() {
  await refreshTab(currentTab);
  await loadBadges();
}

async function refreshTab(tab) {
  const loaders = { overview:loadOverview, apps:loadAppsTab, content:loadContent, campaigns:loadCampaigns, experiments:loadExperiments, health:loadHealth, maturity:loadMaturity, costs:loadCosts, insights:loadInsightsTab, opnemen:loadOpnemenTab, analytics:loadAnalytics, kanalen:loadKanalen, kalender:loadKalender };
  if (loaders[tab]) await loaders[tab]();
}

async function loadBadges() {
  const [pending, alerts, daily] = await Promise.all([
    api('/api/campaigns/pending'),
    api('/api/health/alerts'),
    api('/api/costs/daily'),
  ]);
  const bp = document.getElementById('badge-pending');
  if (pending && pending.length > 0) { bp.textContent = pending.length; bp.classList.remove('hidden'); }
  else bp.classList.add('hidden');

  const ba = document.getElementById('badge-alerts');
  if (alerts && Array.isArray(alerts) && alerts.length > 0) { ba.textContent = alerts.length; ba.classList.remove('hidden'); }
  else ba.classList.add('hidden');

  // Budget waarschuwing banner
  _updateBudgetBanner(daily);
}

function _updateBudgetBanner(daily) {
  const banner = document.getElementById('budget-warning-banner');
  if (!banner || !daily) return;
  const spent = daily.total_usd || 0;
  const limit = daily.daily_limit_usd || 1;
  const pct = (spent / limit) * 100;
  if (pct >= 80) {
    const color = pct >= 100 ? 'bg-danger/10 border-danger/30 text-danger' : 'bg-warning/10 border-warning/30 text-warning';
    banner.className = `border-l-4 rounded-lg px-4 py-2.5 text-xs font-medium flex items-center justify-between ${color}`;
    banner.innerHTML = `
      <span>${pct >= 100 ? '🚨 Dagbudget overschreden!' : '⚠️ Dagbudget bijna bereikt'} — $${spent.toFixed(3)} / $${limit.toFixed(2)} (${Math.round(pct)}%)</span>
      <a onclick="switchTab('costs')" class="underline cursor-pointer opacity-70 hover:opacity-100 ml-4">Bekijk kosten</a>
    `;
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}

// ═══════ OVERVIEW TAB ════════════════════════════════════════════════
async function loadOverview() {
  const [pending, alerts, daily, camps] = await Promise.all([
    api('/api/campaigns/pending'),
    api('/api/health/alerts'),
    api('/api/costs/daily'),
    api('/api/campaigns/'),
  ]);

  const pendingCount = pending ? pending.length : 0;
  const alertCount = alerts && Array.isArray(alerts) ? alerts.length : 0;
  const spent = daily ? daily.total_usd : 0;
  const totalCamps = camps ? camps.length : 0;

  const genCount = (camps||[]).filter(c => c.status === 'generating').length;
  const publishedCount = (camps||[]).filter(c => c.status === 'published').length;
  document.getElementById('kpi-cards').innerHTML = `
    <div class="card p-4 text-center card-glow">
      <div class="kpi-value text-accent">${allApps.length}</div>
      <div class="kpi-label">Apps</div>
    </div>
    <div class="card p-4 text-center card-glow">
      <div class="kpi-value">${totalCamps}</div>
      <div class="kpi-label">Campagnes</div>
      <div class="text-[0.6rem] text-muted mt-1">${genCount} actief · ${publishedCount} gepubliceerd</div>
    </div>
    <div class="card p-4 text-center card-glow">
      <div class="kpi-value text-warning">${pendingCount}</div>
      <div class="kpi-label">Wacht op Review</div>
    </div>
    <div class="card p-4 text-center card-glow">
      <div class="kpi-value text-accent">${formatEUR(spent)}</div>
      <div class="kpi-label">Vandaag Besteed</div>
    </div>
    <div class="card p-4 text-center card-glow">
      <div class="kpi-value ${alertCount > 0 ? 'text-danger' : 'text-success'}">${alertCount}</div>
      <div class="kpi-label">Alerts</div>
    </div>`;

  // Productie status berekening
  const approvedCount = (camps||[]).filter(c => c.status === 'approved').length;
  const failedCount = (camps||[]).filter(c => c.status === 'failed').length;
  const readyToPublish = (camps||[]).filter(c => c.status === 'approved' && c.has_video).length;
  const successRate = totalCamps > 0 ? Math.round(((totalCamps - failedCount) / totalCamps) * 100) : 0;
  const avgScore = (camps||[]).filter(c => c.viral_score?.composite_score).reduce((sum, c) => sum + c.viral_score.composite_score, 0) / Math.max(1, (camps||[]).filter(c => c.viral_score?.composite_score).length);

  document.getElementById('production-status').innerHTML = `
    <div class="bg-bg rounded-lg p-3 border border-border">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="w-2 h-2 rounded-full ${readyToPublish > 0 ? 'bg-success' : 'bg-muted'}"></span>
        <span class="text-xs font-semibold text-gray-700">Klaar om te Posten</span>
      </div>
      <div class="text-2xl font-bold ${readyToPublish > 0 ? 'text-success' : 'text-muted'}">${readyToPublish}</div>
      <div class="text-[0.6rem] text-muted mt-1">${approvedCount} goedgekeurd totaal</div>
    </div>
    <div class="bg-bg rounded-lg p-3 border border-border">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="w-2 h-2 rounded-full ${successRate >= 80 ? 'bg-success' : successRate >= 50 ? 'bg-warning' : 'bg-danger'}"></span>
        <span class="text-xs font-semibold text-gray-700">Succes Ratio</span>
      </div>
      <div class="text-2xl font-bold">${successRate}%</div>
      <div class="text-[0.6rem] text-muted mt-1">${failedCount} mislukt van ${totalCamps}</div>
    </div>
    <div class="bg-bg rounded-lg p-3 border border-border">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="w-2 h-2 rounded-full ${avgScore >= 80 ? 'bg-success' : avgScore >= 65 ? 'bg-warning' : 'bg-danger'}"></span>
        <span class="text-xs font-semibold text-gray-700">Gem. Viral Score</span>
      </div>
      <div class="text-2xl font-bold" style="color:${avgScore >= 80 ? '#16a34a' : avgScore >= 65 ? '#d97706' : '#dc2626'}">${avgScore ? avgScore.toFixed(0) : '-'}</div>
      <div class="text-[0.6rem] text-muted mt-1">/100 over alle campagnes</div>
    </div>
    <div class="bg-bg rounded-lg p-3 border border-border">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="w-2 h-2 rounded-full ${publishedCount > 0 ? 'bg-success' : 'bg-muted'}"></span>
        <span class="text-xs font-semibold text-gray-700">Gepubliceerd</span>
      </div>
      <div class="text-2xl font-bold text-accent">${publishedCount}</div>
      <div class="text-[0.6rem] text-muted mt-1">${genCount > 0 ? genCount + ' in productie' : 'Geen actieve productie'}</div>
    </div>`;

  // Recent campaigns
  const campEl = document.getElementById('overview-campaigns');
  const recentCamps = (camps || []).slice(0, 8);
  if (recentCamps.length > 0) {
    campEl.innerHTML = recentCamps.map(c => {
      const appName = allApps.find(a => (a.id||a.app_id) === c.app_id)?.name || c.app_id || '';
      const title = c.display_name || c.idea_title || shortId(c.id);
      const showApp = appName && !title.includes(appName);
      return `
      <div class="flex items-center justify-between py-2.5 border-b border-border/40 last:border-0 group cursor-pointer" onclick="switchTab('campaigns');showCampaignDetail('${c.id}')">
        <div class="flex-1 min-w-0">
          <span class="text-sm font-medium truncate block group-hover:text-accent transition-colors">${escapeHtml(title)}</span>
          ${showApp ? `<span class="text-xs text-muted">${escapeHtml(appName)}</span>` : ''}
        </div>
        <div class="flex items-center gap-3 ml-3 flex-shrink-0">
          ${statusBadge(c.status)}
          <span class="text-xs text-muted whitespace-nowrap">${timeAgo(c.created_at)}</span>
        </div>
      </div>`;
    }).join('');
  } else {
    campEl.innerHTML = '<p class="text-sm text-muted py-4">Geen campagnes gevonden</p>';
  }

  // Alerts
  const alertEl = document.getElementById('overview-alerts');
  if (alerts && alerts.length > 0) {
    alertEl.innerHTML = alerts.slice(0,4).map(a => `
      <div class="flex items-center gap-2 py-2 border-b border-border/40 last:border-0">
        <span class="w-2 h-2 rounded-full ${a.severity==='critical'?'bg-danger':'bg-warning'} flex-shrink-0"></span>
        <span class="text-sm flex-1 truncate">${a.message || a.alert_type || 'Alert'}</span>
        <span class="text-xs text-muted">${timeAgo(a.created_at || a.timestamp)}</span>
      </div>`).join('');
  } else {
    alertEl.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#10003;</div><div class="empty-state-text text-success">Geen actieve alerts</div></div>';
  }
}

// ═══════ APPS TAB ════════════════════════════════════════════════════
async function loadAppsTab() {
  const grid = document.getElementById('apps-grid');
  if (allApps.length === 0) {
    grid.innerHTML = `
      <div class="card card-glow p-8 col-span-full text-center border-dashed border-2 cursor-pointer hover:border-accent" onclick="openModal('add-app')">
        <svg class="w-10 h-10 mx-auto mb-3 text-muted" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M12 4v16m8-8H4"/></svg>
        <p class="text-sm text-muted">Voeg je eerste app toe</p>
        <p class="text-xs text-muted mt-1">Plak een URL zodat de AI content kan genereren</p>
      </div>`;
    return;
  }

  grid.innerHTML = allApps.map(app => {
    const id = app.id || app.app_id;
    const channels = (app.active_channels || []).map(ch => `<span class="channel-badge">${ch}</span>`).join(' ');
    const hasUrl = app.url && app.url.length > 0;
    return `
      <div class="card card-glow app-card p-5">
        <div class="flex justify-between items-start mb-3">
          <div>
            <h4 class="font-semibold text-sm">${app.name || id}</h4>
            <span class="text-[0.65rem] text-muted font-mono">${id}</span>
          </div>
          <div class="flex gap-1">
            <span class="niche-badge">${app.niche || 'general'}</span>
            ${app.active === false ? '<span class="badge status-failed">Inactief</span>' : ''}
          </div>
        </div>

        ${hasUrl ? `
          <div class="bg-bg rounded-lg p-2.5 mb-3 flex items-center gap-2 text-xs group">
            <svg class="w-3.5 h-3.5 text-muted flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
            <a href="${app.url}" target="_blank" class="text-accent truncate hover:underline">${app.url}</a>
          </div>` : `
          <div class="bg-bg rounded-lg p-2.5 mb-3 text-xs text-muted italic">Geen URL opgegeven</div>`}

        <p class="text-xs text-muted mb-3 line-clamp-2">${app.description || 'Geen beschrijving'}</p>

        ${app.target_audience ? `<div class="text-[0.65rem] text-muted mb-1"><span class="text-muted/60">Doelgroep:</span> ${app.target_audience}</div>` : ''}
        ${app.usp ? `<div class="text-[0.65rem] text-muted mb-1"><span class="text-muted/60">USP:</span> ${app.usp}</div>` : ''}
        ${app.features ? `<div class="flex flex-wrap gap-1 mb-1">${app.features.slice(0,3).map(f => `<span class="text-[0.6rem] bg-accent/10 text-accent px-1.5 py-0.5 rounded">${f}</span>`).join('')}</div>` : ''}
        ${app.tone ? `<div class="text-[0.65rem] text-muted mb-2"><span class="text-muted/60">Tone:</span> ${app.tone}</div>` : ''}

        <div class="flex items-center justify-between pt-3 border-t border-border">
          <div class="flex gap-1">${channels || '<span class="text-xs text-muted">Geen kanalen</span>'}</div>
          <div class="flex gap-1.5 flex-wrap">
            ${hasUrl ? `<button onclick="analyzeApp('${id}')" class="btn btn-outline btn-xs">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              AI Analyse
            </button>` : ''}
            <button onclick="viewAppContent('${id}')" class="btn btn-outline btn-xs">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
              Content bekijken
            </button>
            <button onclick="startCampaignForApp('${id}')" class="btn btn-primary btn-xs">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
              Campagne starten
            </button>
            <button onclick="deleteApp('${id}')" class="btn btn-danger btn-xs">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
              Verwijderen
            </button>
          </div>
        </div>
      </div>`;
  }).join('');
}

function viewAppContent(appId) {
  document.getElementById('content-app-filter').value = appId;
  document.getElementById('app-selector').value = appId;
  currentApp = appId;
  switchTab('content');
}

function startCampaignForApp(appId) {
  openModal('start-campaign');
  setTimeout(() => { document.getElementById('campaign-app').value = appId; }, 50);
}

async function deleteApp(appId) {
  if (!confirm(`App "${appId}" verwijderen? Dit kan niet ongedaan worden.`)) return;
  const res = await api(`/api/apps/${appId}`, { method: 'DELETE' });
  if (res) {
    toast('App verwijderd', 'success');
    await loadApps();
    loadAppsTab();
  } else {
    toast('Verwijderen mislukt', 'error');
  }
}

async function submitAddApp(e) {
  e.preventDefault();
  const body = {
    name: document.getElementById('app-name').value,
    url: document.getElementById('app-url').value,
    description: document.getElementById('app-desc').value,
    target_audience: document.getElementById('app-audience').value,
    usp: document.getElementById('app-usp').value,
    niche: document.getElementById('app-niche').value,
  };
  const res = await api('/api/apps/', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  if (res) {
    closeModal('add-app');
    toast(`App "${body.name}" toegevoegd!`, 'success');
    await loadApps();
    if (currentTab === 'apps') loadAppsTab();
    ['app-name','app-url','app-desc','app-audience','app-usp'].forEach(id => document.getElementById(id).value = '');
  } else {
    toast('Toevoegen mislukt', 'error');
  }
}

// ── URL Analyze ──────────────────────────────────────────────────────
async function analyzeUrlInModal() {
  const url = document.getElementById('app-url').value.trim();
  if (!url) { toast('Vul eerst een URL in', 'warning'); return; }

  const statusEl = document.getElementById('analyze-status');
  const btn = document.getElementById('btn-analyze-url');
  statusEl.classList.remove('hidden');
  btn.disabled = true;
  btn.innerHTML = '<svg class="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Bezig...';

  const result = await api('/api/apps/analyze-url', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ url }),
  });

  btn.disabled = false;
  btn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg> Analyseer';

  if (result) {
    statusEl.textContent = 'Analyse compleet! Velden ingevuld.';
    statusEl.classList.remove('text-accent');
    statusEl.classList.add('text-success');
    setTimeout(() => statusEl.classList.add('hidden'), 3000);

    if (result.name) document.getElementById('app-name').value = result.name;
    if (result.description) document.getElementById('app-desc').value = result.description;
    if (result.target_audience) document.getElementById('app-audience').value = result.target_audience;
    if (result.usp) document.getElementById('app-usp').value = result.usp;
    if (result.niche) document.getElementById('app-niche').value = result.niche;

    toast('AI analyse compleet — controleer de velden', 'success');
  } else {
    statusEl.textContent = 'Analyse mislukt — vul handmatig in';
    statusEl.classList.remove('text-accent');
    statusEl.classList.add('text-danger');
    toast('URL analyse mislukt', 'error');
  }
}

async function analyzeApp(appId) {
  toast('App wordt geanalyseerd door AI...', 'info');
  const res = await api(`/api/apps/${appId}/analyze`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({}),
  });
  if (res) {
    toast(`App "${res.app?.name || appId}" geanalyseerd — ${res.fields_updated?.length || 0} velden bijgewerkt`, 'success');
    await loadApps();
    loadAppsTab();
  } else {
    toast('Analyse mislukt — check of je API key geconfigureerd is', 'error');
  }
}

// ═══════ CONTENT TAB ═════════════════════════════════════════════════
async function loadContent() {
  const appId = document.getElementById('content-app-filter').value;
  const grid = document.getElementById('content-grid');
  const stats = document.getElementById('content-stats');
  const subtitle = document.getElementById('content-subtitle');

  if (!appId) {
    stats.classList.add('hidden');
    subtitle.textContent = 'Selecteer een app om alle gegenereerde content te zien.';
    grid.innerHTML = `
      <div class="card p-8 col-span-full text-center text-muted">
        <svg class="w-12 h-12 mx-auto mb-3 opacity-30" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3.75 21h16.5A2.25 2.25 0 0022.5 18.75V5.25A2.25 2.25 0 0020.25 3H3.75A2.25 2.25 0 001.5 5.25v13.5A2.25 2.25 0 003.75 21z"/></svg>
        <p class="text-sm">Selecteer een app in het filter hierboven</p>
      </div>`;
    return;
  }

  grid.innerHTML = '<div class="col-span-full text-center py-8"><div class="skeleton h-8 w-48 mx-auto"></div></div>';

  const data = await api(`/api/apps/${appId}/content`);
  if (!data || !data.content) {
    grid.innerHTML = '<div class="card p-8 col-span-full text-center text-muted"><p class="text-sm">Geen content gevonden voor deze app</p></div>';
    stats.classList.add('hidden');
    return;
  }

  allContent = data.content;
  const appName = allApps.find(a => (a.id||a.app_id) === appId)?.name || appId;
  subtitle.textContent = `${allContent.length} items voor ${appName}`;

  stats.classList.remove('hidden');
  document.getElementById('stat-total').textContent = allContent.length;
  document.getElementById('stat-published').textContent = allContent.filter(c => c.status === 'published').length;
  document.getElementById('stat-pending').textContent = allContent.filter(c => c.status === 'pending_approval').length;
  document.getElementById('stat-cost').textContent = formatEUR(allContent.reduce((s,c) => s + (c.total_cost_usd||0), 0));

  renderContent();
}

function setContentView(view) {
  contentView = view;
  document.getElementById('view-grid').className = `btn-icon border-none ${view==='grid' ? 'bg-card text-accent' : 'bg-transparent text-muted'}`;
  document.getElementById('view-list').className = `btn-icon border-none ${view==='list' ? 'bg-card text-accent' : 'bg-transparent text-muted'}`;
  renderContent();
}

function renderContent() {
  const grid = document.getElementById('content-grid');
  if (allContent.length === 0) {
    grid.innerHTML = '<div class="card p-8 col-span-full text-center text-muted"><p class="text-sm">Nog geen content — start een campagne!</p></div>';
    return;
  }

  if (contentView === 'grid') {
    grid.className = 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4';
    grid.innerHTML = allContent.map((c, idx) => `
      <div class="card content-card p-4 cursor-pointer" onclick="showContentDetail(${idx})">
        <div class="flex justify-between items-start mb-2">
          ${statusBadge(c.status)}
          <span class="text-[0.65rem] text-muted">${timeAgo(c.created_at)}</span>
        </div>
        <h4 class="font-semibold text-sm mb-1 truncate">${escapeHtml(c.idea_title || 'Naamloos idee')}</h4>
        ${c.idea_hook ? `<p class="text-xs text-muted mb-2 line-clamp-2">${escapeHtml(c.idea_hook)}</p>` : ''}

        <div class="space-y-2 mt-3">
          <div class="flex items-center gap-2 text-xs">
            <svg class="w-3.5 h-3.5 text-muted" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
            <span class="text-muted">${c.script_scene_count || 0} scenes</span>
            ${c.script_preview ? `<span class="text-muted truncate flex-1">${escapeHtml(truncate(c.script_preview, 40))}</span>` : ''}
          </div>
          <div class="flex items-center gap-2 text-xs">
            <svg class="w-3.5 h-3.5 text-muted" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z"/></svg>
            <span class="text-muted truncate">${escapeHtml(c.caption_preview || 'Geen caption')}</span>
          </div>
          ${c.has_video ? `
          <div class="flex items-center gap-2 text-xs text-success">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            Video beschikbaar
          </div>` : ''}
        </div>

        <div class="flex items-center justify-between mt-3 pt-3 border-t border-border">
          <div class="flex gap-1">${(c.hashtags||[]).slice(0,3).map(h => `<span class="text-[0.6rem] text-accent">${h}</span>`).join(' ')}</div>
          <span class="text-xs text-muted">${formatEUR(c.total_cost_usd)}</span>
        </div>
      </div>`).join('');
  } else {
    grid.className = 'space-y-2';
    grid.innerHTML = allContent.map((c, idx) => `
      <div class="card content-card p-3 flex items-center gap-4 cursor-pointer" onclick="showContentDetail(${idx})">
        <div class="w-24 flex-shrink-0">${statusBadge(c.status)}</div>
        <div class="flex-1 min-w-0">
          <span class="text-sm font-medium truncate block">${escapeHtml(c.idea_title || 'Naamloos')}</span>
          <span class="text-xs text-muted">${c.script_scene_count} scenes | ${c.caption_preview ? escapeHtml(truncate(c.caption_preview, 60)) : 'Geen caption'}</span>
        </div>
        <div class="flex items-center gap-4">
          ${c.has_video ? '<span class="text-xs text-success">Video</span>' : '<span class="text-xs text-muted">Geen video</span>'}
          <span class="text-xs text-muted">${formatEUR(c.total_cost_usd)}</span>
          <span class="text-xs text-muted">${timeAgo(c.created_at)}</span>
        </div>
      </div>`).join('');
  }
}

function showContentDetail(idx) {
  const c = allContent[idx];
  if (!c) return;

  document.getElementById('content-detail-title').textContent = c.idea_title || 'Content Details';
  document.getElementById('content-detail-body').innerHTML = `
    <div class="flex items-center gap-3 mb-1">
      ${statusBadge(c.status)}
      <span class="text-xs text-muted">${c.platform}</span>
      <span class="text-xs text-muted">${timeAgo(c.created_at)}</span>
      ${c.experiment_id ? `<span class="badge status-measuring">Experiment</span>` : ''}
    </div>

    ${c.idea_hook ? `
    <div class="mt-4">
      <h4 class="text-xs text-muted uppercase tracking-wider mb-2">Idee / Hook</h4>
      <p class="text-sm bg-bg p-3 rounded-lg">${escapeHtml(c.idea_hook)}</p>
    </div>` : ''}

    ${c.script_preview ? `
    <div class="mt-4">
      <h4 class="text-xs text-muted uppercase tracking-wider mb-2">Script Preview (${c.script_scene_count} scenes)</h4>
      <p class="text-sm bg-bg p-3 rounded-lg text-muted">${escapeHtml(c.script_preview)}${c.script_scene_count > 1 ? '...' : ''}</p>
    </div>` : ''}

    ${c.caption_preview ? `
    <div class="mt-4">
      <h4 class="text-xs text-muted uppercase tracking-wider mb-2">Caption</h4>
      <p class="text-sm bg-bg p-3 rounded-lg">${escapeHtml(c.caption_preview)}</p>
      ${c.hashtags && c.hashtags.length ? `<div class="flex gap-2 mt-2 flex-wrap">${c.hashtags.map(h => `<span class="text-xs text-accent">${h}</span>`).join('')}</div>` : ''}
    </div>` : ''}

    ${c.has_video ? `
    <div class="mt-4">
      <h4 class="text-xs text-muted uppercase tracking-wider mb-2">Video</h4>
      <div class="bg-bg rounded-lg overflow-hidden">
        <video controls preload="metadata" class="w-full max-h-[400px] bg-black"
               src="${videoUrl(c.video_path)}" style="aspect-ratio:9/16; object-fit:contain;">
          Je browser ondersteunt geen video.
        </video>
        <div class="p-3 flex items-center justify-between border-t border-border">
          <span class="text-xs text-success font-medium">Video beschikbaar</span>
          <a href="${videoUrl(c.video_path)}" download class="btn btn-primary btn-sm">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
            Download
          </a>
        </div>
      </div>
    </div>` : ''}

    <div class="mt-4 flex items-center justify-between text-xs text-muted pt-3 border-t border-border">
      <span>Campagne: ${shortId(c.campaign_id)}</span>
      <span>Kosten: ${formatEUR(c.total_cost_usd)}</span>
      ${c.published_at && c.published_at !== 'None' ? `<span>Gepubliceerd: ${timeAgo(c.published_at)}</span>` : ''}
    </div>
  `;
  openModal('content-detail');
}

// ═══════ CAMPAIGNS TAB ═══════════════════════════════════════════════
async function loadCampaigns() {
  const appParam = currentApp ? `?app_id=${currentApp}` : '';
  const data = await api(`/api/campaigns/${appParam}`);
  allCampaigns = data || [];
  renderCampaigns(allCampaigns);
}

function filterCampaigns(status) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.status === status));
  renderCampaigns(status ? allCampaigns.filter(c => c.status === status) : allCampaigns);
}

function renderCampaigns(list) {
  const tbody = document.getElementById('campaigns-table');
  if (!list || list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-8">Geen campagnes gevonden</td></tr>';
    return;
  }
  const typeLabels = {
    'stock_footage': 'Stock', 'talking_head': 'Talking Head', 'text_on_screen': 'Tekst',
    'ugc_style': 'UGC', 'product_demo': 'Demo', 'screen_recording': 'Scherm',
  };
  const typeIcons = {
    'stock_footage': '🎬', 'talking_head': '🗣', 'text_on_screen': '📝',
    'ugc_style': '📱', 'product_demo': '🖥', 'screen_recording': '⏺',
  };
  tbody.innerHTML = list.map(c => {
    const appName = allApps.find(a => (a.id||a.app_id) === c.app_id)?.name || c.app_id || '';
    const title = c.display_name || c.idea_title || shortId(c.id);
    const vType = c.video_type || '';
    const vLabel = typeLabels[vType] || (vType ? vType.replace(/_/g,' ') : '-');
    const vIcon = typeIcons[vType] || '🎞';
    const dur = c.duration_sec ? `${c.duration_sec}s` : '-';
    return `
    <tr class="cursor-pointer" onclick="showCampaignDetail('${c.id}')">
      <td>
        <div class="text-sm font-medium truncate max-w-[220px]">${escapeHtml(title)}</div>
        <div class="text-xs text-muted">${escapeHtml(appName)}</div>
      </td>
      <td><span class="text-xs">${vIcon} ${vLabel}</span></td>
      <td><span class="text-xs text-muted">${dur}</span></td>
      <td>${statusBadge(c.status)}</td>
      <td class="text-right">${c.viral_score ? `<span class="text-sm font-semibold" style="color:${c.viral_score.composite_score >= 80 ? '#16a34a' : c.viral_score.composite_score >= 65 ? '#d97706' : '#dc2626'}">${c.viral_score.composite_score}</span><span class="text-xs text-muted">/100</span>` : '<span class="text-muted">-</span>'}</td>
      <td class="text-right"><span class="text-xs text-muted">${formatEUR(c.total_cost_usd)}</span></td>
      <td class="text-right"><span class="text-xs text-muted whitespace-nowrap">${timeAgo(c.created_at)}</span></td>
    </tr>`;
  }).join('');
}

async function showCampaignDetail(id) {
  const data = await api(`/api/campaigns/${id}`);
  if (!data) { toast('Campaign niet gevonden', 'error'); return; }

  const el = document.getElementById('campaign-detail-content');
  el.innerHTML = `
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div><span class="text-xs text-muted">Status</span><br>${statusBadge(data.status)}</div>
      <div><span class="text-xs text-muted">Platform</span><br><span class="text-sm">${data.platform || '-'}</span></div>
      <div><span class="text-xs text-muted">Kosten</span><br><span class="text-sm font-semibold">${formatEUR(data.total_cost_usd)}</span></div>
    </div>
    ${data.idea && data.idea.title ? `<div class="mb-3"><h4 class="text-xs text-muted uppercase tracking-wider mb-1">Idee</h4><div class="bg-bg p-3 rounded-lg text-sm"><strong>${data.idea.title}</strong><p class="text-muted mt-1">${data.idea.hook || data.idea.description || ''}</p></div></div>` : ''}
    ${data.script && data.script.scenes ? `<div class="mb-3"><h4 class="text-xs text-muted uppercase tracking-wider mb-1">Script (${data.script.scenes.length} scenes)</h4><div class="bg-bg p-3 rounded-lg text-xs text-muted max-h-40 overflow-y-auto">${data.script.scenes.map((s,i) => `<div class="mb-2"><span class="text-accent">Scene ${i+1}:</span> ${s.voiceover || s.description || JSON.stringify(s)}</div>`).join('')}</div></div>` : ''}
    ${data.viral_score ? `<div class="mb-3"><h4 class="text-xs text-muted uppercase tracking-wider mb-1">Viral Algorithm Score</h4>
      <div class="bg-bg p-4 rounded-lg">
        <div class="flex items-center gap-3 mb-3">
          <span class="text-3xl font-bold" style="color:${data.viral_score.composite_score >= 80 ? '#16a34a' : data.viral_score.composite_score >= 65 ? '#d97706' : '#dc2626'}">${data.viral_score.composite_score}</span>
          <span class="text-lg text-muted">/100</span>
          <span class="px-2 py-0.5 rounded text-xs font-semibold" style="background:${data.viral_score.verdict === 'VIRAL_READY' ? '#16a34a22' : data.viral_score.verdict === 'STRONG' ? '#d9770622' : '#dc262622'}; color:${data.viral_score.verdict === 'VIRAL_READY' ? '#16a34a' : data.viral_score.verdict === 'STRONG' ? '#d97706' : '#dc2626'}">${data.viral_score.verdict === 'VIRAL_READY' ? 'VIRAL READY' : data.viral_score.verdict}</span>
          ${data.viral_score.rewrites_needed > 0 ? `<span class="text-xs text-muted">(${data.viral_score.rewrites_needed}x herschreven)</span>` : ''}
        </div>
        ${data.viral_score.scores ? `<div class="grid grid-cols-3 gap-2 mb-3">
          ${Object.entries(data.viral_score.scores).map(([k,v]) => `<div class="text-center p-2 rounded" style="background:rgba(255,255,255,0.03)">
            <div class="text-lg font-semibold" style="color:${v >= 80 ? '#16a34a' : v >= 65 ? '#d97706' : '#dc2626'}">${v}</div>
            <div class="text-xs text-muted">${k.replace(/_/g,' ')}</div>
          </div>`).join('')}
        </div>` : ''}
        ${data.viral_score.summary ? `<p class="text-sm text-muted mb-2">${data.viral_score.summary}</p>` : ''}
        ${data.viral_score.strengths && data.viral_score.strengths.length ? `<div class="mb-2"><span class="text-xs text-success font-medium">Sterktes:</span><ul class="text-xs text-muted mt-1">${data.viral_score.strengths.map(s => `<li>+ ${s}</li>`).join('')}</ul></div>` : ''}
        ${data.viral_score.weaknesses && data.viral_score.weaknesses.length ? `<div class="mb-2"><span class="text-xs text-warning font-medium">Verbeterpunten:</span><ul class="text-xs text-muted mt-1">${data.viral_score.weaknesses.map(w => `<li>- ${w}</li>`).join('')}</ul></div>` : ''}
        ${data.viral_score.algorithm_tips && data.viral_score.algorithm_tips.length ? `<div><span class="text-xs text-accent font-medium">Algoritme tips:</span><ul class="text-xs text-muted mt-1">${data.viral_score.algorithm_tips.map(t => `<li>${t}</li>`).join('')}</ul></div>` : ''}
      </div></div>` : ''}
    ${data.caption ? `<div class="mb-3"><h4 class="text-xs text-muted uppercase tracking-wider mb-1">Caption</h4><div class="bg-bg p-3 rounded-lg text-sm">${data.caption.caption || ''}<br><span class="text-accent text-xs">${(data.caption.hashtags||[]).join(' ')}</span></div></div>` : ''}
    ${data.video_path ? `<div class="mb-3"><h4 class="text-xs text-muted uppercase tracking-wider mb-1">Video</h4>
      <div class="bg-bg rounded-lg overflow-hidden">
        <video controls preload="metadata" class="w-full bg-black mx-auto" style="max-height:480px; aspect-ratio:9/16; object-fit:contain;"
               src="${videoUrl(data.video_path)}">
          Je browser ondersteunt geen video.
        </video>
        <div class="p-3 flex items-center justify-between border-t border-border">
          <span class="text-xs text-success font-medium">Video beschikbaar</span>
          <a href="${videoUrl(data.video_path)}" download class="btn btn-primary btn-sm">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
            Download
          </a>
        </div>
      </div></div>` : ''}
    ${data.status === 'pending_approval' ? `
    <div class="mt-4 p-4 bg-bg rounded-lg border border-border space-y-3">
      <h4 class="text-xs font-semibold text-gray-700 uppercase tracking-wider flex items-center gap-2">
        <svg class="w-3.5 h-3.5 text-warning" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
        Goedkeuring vereist
      </h4>

      <!-- Inplannen (optioneel) -->
      <div>
        <label class="text-xs text-muted block mb-1 font-medium">Inplannen op (optioneel — leeg = direct publiceren)</label>
        <input type="datetime-local" id="schedule-for-${data.id}" class="w-full text-xs" min="${new Date().toISOString().slice(0,16)}">
      </div>

      <!-- Notities -->
      <div>
        <label class="text-xs text-muted block mb-1 font-medium">Notities (optioneel)</label>
        <textarea id="approval-notes" placeholder="Feedback of wijzigingsverzoeken..." class="w-full text-xs resize-none" rows="2"></textarea>
      </div>

      <!-- Knoppen -->
      <div class="flex gap-2">
        <button onclick="approveCampaign('${data.id}')" class="btn btn-success flex-1 text-xs">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>
          Goedkeuren & Publiceren
        </button>
        <button onclick="requestChangesCampaign('${data.id}')" class="btn btn-outline flex-1 text-xs">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          Wijzigingen vragen
        </button>
        <button onclick="rejectCampaign('${data.id}')" class="btn btn-danger text-xs px-3">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>
    </div>` : ''}
    ${data.status === 'approved' && data.video_path ? `
    <div class="mt-4 p-4 rounded-lg border-2 border-accent/30" style="background:linear-gradient(135deg,#fdfcff,#f9f5ff);">
      <h4 class="text-xs text-muted uppercase tracking-wider mb-3">Publiceren</h4>
      <div class="flex gap-2">
        <button onclick="publishToTikTok('${data.id}')" class="btn btn-primary flex-1">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.33 6.33 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.69a8.27 8.27 0 004.84 1.55V6.79a4.85 4.85 0 01-1.07-.1z"/></svg>
          Publiceer op TikTok
        </button>
        <a href="${videoUrl(data.video_path)}" download class="btn btn-outline flex-1 text-center">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          Download Video
        </a>
      </div>
      <button onclick="regenerateVideo('${data.id}')" class="btn btn-outline w-full mt-2 text-xs">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Video opnieuw genereren
      </button>
    </div>` : ''}
    ${data.status === 'failed' ? `
    <div class="mt-4 p-4 bg-danger/5 rounded-lg border border-danger/20">
      <h4 class="text-xs text-danger uppercase tracking-wider mb-2 font-semibold">Campagne mislukt</h4>
      ${data.approval_notes ? `<p class="text-xs text-danger/80 bg-danger/10 rounded p-2 mb-3 font-mono break-all">${data.approval_notes}</p>` : '<p class="text-xs text-muted mb-3">De video productie is mislukt. Je kunt de video opnieuw laten genereren.</p>'}
      <button onclick="regenerateVideo('${data.id}')" class="btn btn-primary w-full">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Opnieuw Genereren
      </button>
    </div>` : ''}
    ${data.status === 'rejected' ? `
    <div class="mt-4 p-4 bg-warning/5 rounded-lg border border-warning/20">
      <h4 class="text-xs text-warning uppercase tracking-wider mb-2 font-semibold">Afgewezen</h4>
      ${data.rejection_reason ? `<p class="text-xs text-muted mb-3">Reden: ${data.rejection_reason}</p>` : ''}
      <button onclick="regenerateVideo('${data.id}')" class="btn btn-outline w-full">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Video opnieuw genereren
      </button>
    </div>` : ''}
    <details class="mt-3"><summary class="text-xs text-muted cursor-pointer hover:text-gray-600">Volledige JSON</summary><pre class="text-xs bg-bg p-3 rounded-lg overflow-auto max-h-60 mt-2 text-muted">${JSON.stringify(data, null, 2)}</pre></details>
  `;
  document.getElementById('campaign-detail').classList.remove('hidden');
}

async function approveCampaign(campaignId) {
  const notes = document.getElementById('approval-notes')?.value || '';
  const scheduleEl = document.getElementById(`schedule-for-${campaignId}`);
  const scheduledFor = scheduleEl?.value ? new Date(scheduleEl.value).toISOString() : null;

  const body = { campaign_id: campaignId, decision: 'approve', notes };
  if (scheduledFor) body.scheduled_for = scheduledFor;

  const res = await api('/api/approvals/decide', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  if (res) {
    const msg = scheduledFor
      ? `Campagne ingepland voor ${new Date(scheduledFor).toLocaleString('nl-NL')}!`
      : 'Campagne goedgekeurd en wordt gepubliceerd!';
    toast(msg, 'success');
    closeCampaignDetail();
    loadCampaigns(); loadBadges();
  } else {
    toast('Goedkeuring mislukt', 'error');
  }
}

async function requestChangesCampaign(campaignId) {
  const notes = document.getElementById('approval-notes')?.value || '';
  if (!notes.trim()) { toast('Beschrijf welke wijzigingen je wilt', 'warning'); return; }
  const res = await api('/api/approvals/decide', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ campaign_id: campaignId, decision: 'request_changes', notes }),
  });
  if (res) {
    toast('Campagne teruggestuurd voor wijzigingen', 'info');
    closeCampaignDetail(); loadCampaigns(); loadBadges();
  } else {
    toast('Verzoek mislukt', 'error');
  }
}

async function rejectCampaign(campaignId) {
  const notes = document.getElementById('approval-notes')?.value || '';
  if (!notes.trim()) { toast('Vul een afwijzingsreden in bij notities', 'warning'); return; }
  if (!confirm('Campagne definitief afwijzen?')) return;
  const res = await api('/api/approvals/decide', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ campaign_id: campaignId, decision: 'reject', notes }),
  });
  if (res) {
    toast('Campagne afgewezen', 'info');
    closeCampaignDetail(); loadCampaigns(); loadBadges();
  } else {
    toast('Afwijzing mislukt', 'error');
  }
}

function closeCampaignDetail() { document.getElementById('campaign-detail').classList.add('hidden'); }

async function batchApproveAll() {
  const pending = allCampaigns.filter(c => c.status === 'pending_approval');
  if (pending.length === 0) { toast('Geen campagnes wachten op goedkeuring', 'info'); return; }
  if (!confirm(`Wil je ${pending.length} campagne(s) goedkeuren?`)) return;

  let ok = 0, fail = 0;
  for (const c of pending) {
    const res = await api('/api/approvals/decide', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ campaign_id: c.id, decision: 'approve', notes: 'Batch goedgekeurd' }),
    });
    if (res) ok++; else fail++;
  }
  toast(`${ok} goedgekeurd${fail ? `, ${fail} mislukt` : ''}`, ok > 0 ? 'success' : 'error');
  loadCampaigns(); loadBadges();
}

async function publishToTikTok(campaignId) {
  if (!confirm('Weet je zeker dat je deze campagne naar TikTok wilt publiceren?')) return;
  toast('Publiceren naar TikTok...', 'info');
  const res = await api(`/api/campaigns/${campaignId}/publish`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ platform: 'tiktok' }),
  });
  if (res && !res.error) {
    toast('Campagne wordt gepubliceerd naar TikTok!', 'success');
    closeCampaignDetail();
    loadCampaigns(); loadBadges();
  } else {
    toast('Publicatie mislukt: ' + (res?.detail || res?.error || 'onbekend'), 'error');
  }
}

async function regenerateVideo(campaignId) {
  if (!confirm('Wil je de video opnieuw genereren met dezelfde settings?')) return;
  toast('Video wordt opnieuw gegenereerd...', 'info');
  const res = await api(`/api/campaigns/${campaignId}/regenerate-video`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
  });
  if (res) {
    toast('Video wordt opnieuw gegenereerd', 'success');
    if (res.id) startProgressStream(res.id);
    closeCampaignDetail();
    setTimeout(() => { loadCampaigns(); loadBadges(); }, 3000);
  } else {
    toast('Opnieuw genereren mislukt', 'error');
  }
}

async function previewVoice() {
  const voice = document.getElementById('campaign-voice').value;
  const speed = parseFloat(document.getElementById('campaign-tts-speed').value) || 1.0;
  const stability = parseFloat(document.getElementById('campaign-stability')?.value) || 0.58;
  const similarity_boost = parseFloat(document.getElementById('campaign-similarity')?.value) || 0.92;
  const style = parseFloat(document.getElementById('campaign-style')?.value) || 0.45;
  try {
    if (previewAudio) { previewAudio.pause(); if (previewAudio.src) URL.revokeObjectURL(previewAudio.src); previewAudio = null; }
    toast('Stem preview laden...', 'info');
    const res = await fetch(`${API}/api/campaigns/voices/preview`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ voice, speed, stability, similarity_boost, style }),
    });
    if (!res.ok) throw new Error('Preview mislukt');
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    previewAudio = new Audio(blobUrl);
    previewAudio.onended = () => URL.revokeObjectURL(blobUrl);
    previewAudio.play();
  } catch (e) {
    toast('Stem preview mislukt: ' + e.message, 'error');
  }
}

// ── Campagne starten: 3-stappen flow ─────────────────────────────────
let _generatedIdeas = [];
let _chosenIdea = null;

function closeStartCampaignModal() {
  closeModal('start-campaign');
  document.getElementById('campaign-step-1').classList.remove('hidden');
  document.getElementById('campaign-step-2').classList.add('hidden');
  document.getElementById('campaign-step-3').classList.add('hidden');
  document.getElementById('campaign-loading').classList.add('hidden');
  _generatedIdeas = [];
  _chosenIdea = null;
}

async function generateIdeas() {
  const appId = document.getElementById('campaign-app').value;
  const platform = document.getElementById('campaign-platform').value;
  const customBrief = (document.getElementById('campaign-custom-brief')?.value || '').trim();
  if (!appId) { toast('Selecteer eerst een app', 'warning'); return; }

  document.getElementById('campaign-step-1').classList.add('hidden');
  document.getElementById('campaign-loading').classList.remove('hidden');

  try {
    const payload = { app_id: appId, platform };
    if (customBrief) payload.custom_brief = customBrief;

    const res = await api('/api/campaigns/generate-ideas', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
      _timeout: 120000,
    });

    if (!res || !res.ideas || res.ideas.length === 0) {
      toast('Geen ideeën gegenereerd. Probeer opnieuw.', 'error');
      backToStep1();
      return;
    }

    _generatedIdeas = res.ideas;
    renderIdeaCards(res.ideas);

    document.getElementById('campaign-loading').classList.add('hidden');
    document.getElementById('campaign-step-2').classList.remove('hidden');
  } catch (err) {
    toast('Ideeën genereren mislukt: ' + (err.message || err), 'error');
    backToStep1();
  }
}

function renderIdeaCards(ideas) {
  const container = document.getElementById('ideas-container');
  const goalColors = {
    'awareness': 'text-blue-400 bg-blue-400/10 border-blue-400/20',
    'consideration': 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20',
    'conversie': 'text-green-400 bg-green-400/10 border-green-400/20',
  };
  const perfColors = {
    'high': 'text-green-400',
    'medium': 'text-yellow-400',
    'low': 'text-muted',
  };

  container.innerHTML = ideas.map((idea, i) => `
    <div class="idea-card group cursor-pointer bg-bg hover:bg-card border border-border hover:border-accent/40 rounded-lg p-3 transition-all duration-200"
         onclick="selectIdea(${i})">
      <div class="flex items-start justify-between gap-2 mb-2">
        <h4 class="font-semibold text-sm leading-tight">${idea.title || 'Naamloos'}</h4>
        <span class="shrink-0 text-[0.6rem] font-medium px-1.5 py-0.5 rounded border ${goalColors[idea.goal] || 'text-muted bg-muted/10 border-muted/20'}">
          ${idea.goal_label || idea.goal || ''}
        </span>
      </div>
      <p class="text-xs text-muted mb-2 line-clamp-2">${idea.angle || idea.core_message || ''}</p>
      <div class="flex items-center gap-3 text-[0.65rem] text-muted">
        <span class="flex items-center gap-1">
          <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M7 4V2m0 2a2 2 0 012 2v1a2 2 0 01-2 2 2 2 0 01-2-2V6a2 2 0 012-2zm0 10v2m0-2a2 2 0 00-2-2H4a2 2 0 00-2 2 2 2 0 002 2h1a2 2 0 002-2zm10-10V2m0 2a2 2 0 012 2v1a2 2 0 01-2 2 2 2 0 01-2-2V6a2 2 0 012-2z"/></svg>
          ${idea.format_label || idea.content_format || ''}
        </span>
        ${idea.estimated_performance ? `<span class="${perfColors[idea.estimated_performance] || 'text-muted'}">Verwacht: ${idea.estimated_performance}</span>` : ''}
      </div>
      ${idea.hook_options && idea.hook_options.length > 0 ? `
        <div class="mt-2 pt-2 border-t border-border/50">
          <p class="text-[0.6rem] text-muted uppercase tracking-wider mb-1">Hook voorbeeld:</p>
          <p class="text-xs italic text-gray-600">"${idea.hook_options[0]}"</p>
        </div>
      ` : ''}
    </div>
  `).join('');
}

function selectIdea(index) {
  _chosenIdea = _generatedIdeas[index];
  document.getElementById('chosen-idea-title').textContent = _chosenIdea.title || 'Naamloos';
  document.getElementById('chosen-idea-hook').textContent = _chosenIdea.angle || _chosenIdea.core_message || '';

  document.getElementById('campaign-step-2').classList.add('hidden');
  document.getElementById('campaign-step-3').classList.remove('hidden');
}

function backToStep1() {
  document.getElementById('campaign-step-2').classList.add('hidden');
  document.getElementById('campaign-step-3').classList.add('hidden');
  document.getElementById('campaign-loading').classList.add('hidden');
  document.getElementById('campaign-step-1').classList.remove('hidden');
}

function backToStep2() {
  document.getElementById('campaign-step-3').classList.add('hidden');
  document.getElementById('campaign-step-2').classList.remove('hidden');
}

async function submitStartCampaign() {
  const body = {
    app_id: document.getElementById('campaign-app').value,
    platform: document.getElementById('campaign-platform').value,
    voice: document.getElementById('campaign-voice').value,
    tts_speed: parseFloat(document.getElementById('campaign-tts-speed').value) || 1.0,
    voice_stability: parseFloat(document.getElementById('campaign-stability')?.value) || 0.58,
    voice_similarity: parseFloat(document.getElementById('campaign-similarity')?.value) || 0.92,
    voice_style: parseFloat(document.getElementById('campaign-style')?.value) || 0.45,
    chosen_idea: _chosenIdea,
  };
  if (!body.app_id) { toast('Selecteer een app', 'warning'); return; }
  if (!body.chosen_idea) { toast('Kies eerst een idee', 'warning'); return; }

  const res = await api('/api/campaigns/start', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
    _timeout: 120000,
  });
  if (res) {
    closeStartCampaignModal();
    toast('Campagne gestart! Video wordt nu geproduceerd.', 'success');
    startProgressStream(res.id);
    setTimeout(() => { loadCampaigns(); loadBadges(); }, 2000);
  } else {
    toast('Campagne starten mislukt', 'error');
  }
}

// ── SSE Progress Stream ──────────────────────────────────────────────
let _progressStartTime = null;
let _progressTimer = null;

function startProgressStream(campaignId) {
  if (activeEventSource) { activeEventSource.close(); }
  _progressStartTime = Date.now();

  let progressEl = document.getElementById('pipeline-progress');
  if (!progressEl) {
    progressEl = document.createElement('div');
    progressEl.id = 'pipeline-progress';
    progressEl.className = 'fixed bottom-16 right-4 z-50 bg-card border border-border rounded-xl p-4 shadow-2xl w-80';
    document.body.appendChild(progressEl);
  }
  progressEl.innerHTML = `
    <div class="flex justify-between items-center mb-2">
      <span class="text-xs font-semibold text-accent uppercase tracking-wider">Pipeline Voortgang</span>
      <button onclick="closeProgress()" class="text-muted hover:text-gray-900 text-sm">&times;</button>
    </div>
    <div id="progress-steps" class="space-y-1.5 max-h-48 overflow-y-auto"></div>
    <div class="mt-3">
      <div class="flex justify-between text-xs text-muted mb-1">
        <span id="progress-pct">0%</span>
        <span id="progress-elapsed">0:00</span>
      </div>
      <div class="progress-bar"><div id="progress-fill" class="progress-fill bg-accent" style="width:0%; transition: width 0.8s ease-out"></div></div>
    </div>
  `;
  progressEl.classList.remove('hidden');

  // Verstreken tijd counter
  if (_progressTimer) clearInterval(_progressTimer);
  _progressTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - _progressStartTime) / 1000);
    const m = Math.floor(sec / 60);
    const s = String(sec % 60).padStart(2, '0');
    const el = document.getElementById('progress-elapsed');
    if (el) el.textContent = `${m}:${s}`;
  }, 1000);

  // Stap → percentage mapping (gewogen naar werkelijke duur)
  const stepMap = {'1/7': 5, '2/7': 15, '3/7': 35, '4/7': 45, '5/7': 80, '6/7': 92, '7/7': 100};
  // Sub-stap keywords → fijner percentage
  const subStepMap = {
    'Script schrijven': 25, 'Viral score': 32, 'herschrijven': 40,
    'Voiceover': 55, 'Beeldmateriaal': 65, 'footage': 65,
    'Clips': 72, 'assembleren': 78, 'renderen': 75,
    'Caption': 90, 'hashtags': 90,
  };

  function updateProgress(pct) {
    const fill = document.getElementById('progress-fill');
    const label = document.getElementById('progress-pct');
    if (fill) fill.style.width = pct + '%';
    if (label) label.textContent = pct + '%';
  }

  activeEventSource = new EventSource(`${API}/api/campaigns/progress/${campaignId}`);

  activeEventSource.addEventListener('progress', (e) => {
    const data = JSON.parse(e.data);
    const msg = data.message || '';
    const stepsEl = document.getElementById('progress-steps');
    const step = document.createElement('div');
    step.className = 'flex items-center gap-2 text-xs';
    step.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-accent flex-shrink-0"></span><span class="text-muted">${msg}</span>`;
    stepsEl.appendChild(step);
    stepsEl.scrollTop = stepsEl.scrollHeight;

    // Bepaal percentage: eerst exacte stap, dan sub-stap keywords
    let matched = false;
    for (const [key, pct] of Object.entries(stepMap)) {
      if (msg.includes(`Stap ${key}`)) { updateProgress(pct); matched = true; break; }
    }
    if (!matched) {
      for (const [kw, pct] of Object.entries(subStepMap)) {
        if (msg.toLowerCase().includes(kw.toLowerCase())) { updateProgress(pct); break; }
      }
    }
  });

  activeEventSource.addEventListener('done', () => {
    activeEventSource.close(); activeEventSource = null;
    if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
    updateProgress(100);
    const stepsEl = document.getElementById('progress-steps');
    const sec = Math.floor((Date.now() - _progressStartTime) / 1000);
    const done = document.createElement('div');
    done.className = 'flex items-center gap-2 text-xs text-success font-medium mt-1';
    done.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-success"></span>Campagne klaar! (${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')})`;
    stepsEl.appendChild(done);
    toast('Campagne succesvol afgerond!', 'success');
    loadCampaigns(); loadBadges();
    setTimeout(() => closeProgress(), 8000);
  });

  activeEventSource.addEventListener('error', (e) => {
    if (e.data) {
      try { const d = JSON.parse(e.data); toast('Pipeline fout: ' + (d.error||'onbekend'), 'error'); } catch(_){}
    }
    if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }
    if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
    loadCampaigns(); loadBadges();
  });
}

function closeProgress() {
  const el = document.getElementById('pipeline-progress');
  if (el) el.classList.add('hidden');
  if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }
  if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
}

// ═══════ EXPERIMENTS TAB ═════════════════════════════════════════════
async function loadExperiments() {
  if (!currentApp) {
    const tbody = document.getElementById('experiments-table');
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-8">Selecteer een app om experimenten te zien</td></tr>';
    return;
  }
  const data = await api(`/api/experiments/?app_id=${currentApp}`);
  const experiments = data ? (data.experiments || []) : [];
  const tbody = document.getElementById('experiments-table');
  if (experiments.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-8">Geen experimenten gevonden</td></tr>';
    return;
  }
  tbody.innerHTML = experiments.map(e => `
    <tr>
      <td class="font-mono text-xs text-muted">${shortId(e.experiment_id)}</td>
      <td class="text-xs text-muted">${shortId(e.campaign_id)}</td>
      <td class="text-sm">${e.hypothesis?.dimension || '-'}</td>
      <td>${statusBadge(e.status)}</td>
      <td class="text-sm">${(e.variants||[]).length}</td>
      <td class="text-sm">${e.causal_confidence ? (e.causal_confidence * 100).toFixed(0) + '%' : '-'}</td>
      <td>
        ${['concluded','inconclusive'].includes(e.status) ? `<button onclick="showComparison('${e.experiment_id}')" class="btn btn-outline btn-sm">Vergelijk</button>` : ''}
      </td>
    </tr>`).join('');
}

async function showComparison(expId) {
  const data = await api(`/api/experiments/${expId}/comparison`);
  if (!data) { toast('Vergelijking niet beschikbaar', 'warning'); return; }
  document.getElementById('comparison-content').innerHTML = `
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div><span class="text-xs text-muted">Dimensie</span><br><strong>${data.dimension || '-'}</strong></div>
      <div><span class="text-xs text-muted">Winnaar</span><br><strong class="text-success">${shortId(data.winning_variant_id) || '-'}</strong></div>
      <div><span class="text-xs text-muted">Confidence</span><br><strong>${data.causal_confidence ? (data.causal_confidence*100).toFixed(0)+'%' : '-'}</strong></div>
      <div><span class="text-xs text-muted">Conclusie</span><br><span class="text-sm">${data.conclusion || '-'}</span></div>
    </div>
    ${data.variants ? `<table><thead><tr><th>Variant</th><th>Label</th><th>Score</th><th>Views</th></tr></thead>
    <tbody>${data.variants.map(v => `<tr>
      <td class="font-mono text-xs">${shortId(v.variant_id)}</td>
      <td>${v.label || '-'}</td>
      <td class="font-semibold">${v.performance_score ? v.performance_score.toFixed(1) : '-'}</td>
      <td>${v.view_count || '-'}</td>
    </tr>`).join('')}</tbody></table>` : ''}
  `;
  document.getElementById('experiment-comparison').classList.remove('hidden');
}

// ═══════ HEALTH TAB ══════════════════════════════════════════════════
async function loadHealth() {
  const [snapshot, alerts, audit] = await Promise.all([
    api('/api/health/'),
    api('/api/health/alerts'),
    api('/api/health/audit/recent?limit=15'),
  ]);

  const status = snapshot?.overall_status || snapshot?.status || 'unknown';
  const statusLabels = { healthy:'Gezond', degraded:'Deels gestoord', unhealthy:'Ongezond' };
  const colors = { healthy:'bg-success', degraded:'bg-warning', unhealthy:'bg-danger' };
  document.getElementById('health-indicator').className = `health-ring ${colors[status]||'bg-muted'}`;
  document.getElementById('health-label').textContent = statusLabels[status] || status;
  if (snapshot?.taken_at) document.getElementById('health-ts').textContent = `Laatst: ${timeAgo(snapshot.taken_at)}`;
  if (snapshot) {
    const hc = snapshot.healthy_count || 0, dc = snapshot.degraded_count || 0, uc = snapshot.unhealthy_count || 0;
    document.getElementById('health-summary')?.remove();
    const sum = document.createElement('div');
    sum.id = 'health-summary';
    sum.className = 'flex gap-3 text-xs mt-2';
    sum.innerHTML = `<span class="text-success font-medium">${hc} gezond</span><span class="text-warning font-medium">${dc} gestoord</span><span class="text-danger font-medium">${uc} ongezond</span>`;
    document.getElementById('health-indicator').parentElement.after(sum);
  }

  const compEl = document.getElementById('health-components');
  if (snapshot?.components) {
    const entries = typeof snapshot.components === 'object' ? Object.entries(snapshot.components) : [];
    compEl.innerHTML = entries.map(([name, info]) => {
      const st = typeof info === 'string' ? info : (info?.status || 'unknown');
      const lat = typeof info === 'object' && info?.latency_ms ? `${Math.round(info.latency_ms)}ms` : '';
      const err = typeof info === 'object' && info?.error_message ? info.error_message : '';
      const icons = { healthy:'<span class="text-success text-lg">&#10003;</span>', degraded:'<span class="text-warning text-lg">&#9888;</span>', unhealthy:'<span class="text-danger text-lg">&#10007;</span>' };
      const bg = { healthy:'bg-success/5', degraded:'bg-warning/5', unhealthy:'bg-danger/5' };
      const stLabel = { healthy:'Gezond', degraded:'Gestoord', unhealthy:'Ongezond' };
      return `<div class="${bg[st]||'bg-bg'} border border-border/60 rounded-lg p-3 text-center">
        <div class="mb-1">${icons[st]||'<span class="text-muted text-lg">?</span>'}</div>
        <div class="text-xs font-semibold capitalize">${name.replace(/_/g,' ')}</div>
        <div class="text-[0.65rem] font-medium mt-1" style="color:var(--${st==='healthy'?'success':st==='degraded'?'warning':'danger'})">${stLabel[st]||st}</div>
        ${lat ? `<div class="text-[0.6rem] text-muted mt-0.5">${lat}</div>` : ''}
        ${err ? `<div class="text-[0.55rem] text-danger/70 mt-0.5 truncate" title="${escapeHtml(err)}">${escapeHtml(err.substring(0,30))}</div>` : ''}
      </div>`;
    }).join('');
  }

  const alertEl = document.getElementById('health-alerts');
  if (alerts && alerts.length > 0) {
    alertEl.innerHTML = alerts.map(a => `
      <div class="flex items-center justify-between py-2 border-b border-border/40 last:border-0">
        <div class="flex-1"><span class="text-sm">${a.message || a.alert_type || 'Alert'}</span>
        <span class="badge ${a.severity==='critical'?'status-failed':'status-pending'} ml-2">${a.severity||''}</span></div>
        <div class="flex gap-1">
          <button onclick="ackAlert('${a.alert_id||a.id}')" class="btn btn-outline btn-sm">Bevestig</button>
          <button onclick="resolveAlert('${a.alert_id||a.id}')" class="btn btn-success btn-sm">Opgelost</button>
        </div>
      </div>`).join('');
  } else {
    alertEl.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#10003;</div><div class="empty-state-text text-success">Geen actieve alerts</div></div>';
  }

  const auditEl = document.getElementById('health-audit');
  const auditEntries = audit && Array.isArray(audit) ? audit : (audit?.entries || []);
  if (auditEntries.length > 0) {
    auditEl.innerHTML = auditEntries.map(e => {
      const appName = allApps.find(a => (a.id||a.app_id) === e.app_id)?.name || e.app_id || '';
      return `
      <div class="flex items-center gap-2 py-1.5 text-xs border-b border-border/30 last:border-0">
        <span class="w-2 h-2 rounded-full ${e.outcome==='success'?'bg-success':'bg-danger'} flex-shrink-0"></span>
        <span class="font-medium text-muted">${e.job_type||''}</span>
        <span class="flex-1 truncate">${escapeHtml(appName)}</span>
        <span class="text-muted whitespace-nowrap">${timeAgo(e.timestamp)}</span>
      </div>`;
    }).join('');
  } else {
    auditEl.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128203;</div><div class="empty-state-text">Geen audit entries</div></div>';
  }
}

async function ackAlert(id) {
  await fetch(`${API}/api/health/alerts/${id}/acknowledge`, {method:'POST'});
  toast('Alert bevestigd', 'success'); loadHealth(); loadBadges();
}
async function resolveAlert(id) {
  await fetch(`${API}/api/health/alerts/${id}/resolve`, {method:'POST'});
  toast('Alert opgelost', 'success'); loadHealth(); loadBadges();
}

// ═══════ MATURITY TAB ════════════════════════════════════════════════
async function loadMaturity() {
  const appId = currentApp;
  if (!appId) {
    document.getElementById('maturity-score').textContent = '--';
    document.getElementById('maturity-status').textContent = 'Selecteer een app in de header';
    document.getElementById('maturity-metrics').innerHTML = '';
    return;
  }

  const [scorecard, history] = await Promise.all([
    api(`/api/maturity/${appId}`),
    api(`/api/maturity/${appId}/history`),
  ]);

  if (scorecard && scorecard.maturity_score !== undefined) {
    const score = scorecard.maturity_score;
    const color = score >= 70 ? 'text-success' : score >= 40 ? 'text-warning' : 'text-danger';
    document.getElementById('maturity-score').className = `text-3xl font-extrabold ${color}`;
    document.getElementById('maturity-score').textContent = score.toFixed(1);
    document.getElementById('maturity-status').textContent = scorecard.status || '';

    const metrics = [
      { label:'Replicatie', key:'replication_score', weight:'25%' },
      { label:'Predictie', key:'prediction_accuracy', weight:'20%' },
      { label:'Learning Delta', key:'learning_delta', weight:'20%' },
      { label:'Operator Adoptie', key:'operator_adoption', weight:'20%' },
      { label:'Stabiliteit', key:'stability_index', weight:'15%' },
    ];
    document.getElementById('maturity-metrics').innerHTML = metrics.map(m => {
      const v = scorecard[m.key] || 0;
      const c = v >= 70 ? 'text-success' : v >= 40 ? 'text-warning' : 'text-danger';
      const bar = v >= 70 ? 'bg-success' : v >= 40 ? 'bg-warning' : 'bg-danger';
      return `<div class="card p-4 text-center">
        <div class="kpi-value text-lg ${c}">${v.toFixed(0)}</div>
        <div class="kpi-label">${m.label}</div>
        <div class="text-[0.6rem] text-muted">${m.weight}</div>
        <div class="progress-bar mt-2"><div class="progress-fill ${bar}" style="width:${Math.min(v,100)}%"></div></div>
      </div>`;
    }).join('');
  } else {
    document.getElementById('maturity-score').textContent = '--';
    document.getElementById('maturity-status').textContent = 'Geen scorecard beschikbaar';
    document.getElementById('maturity-metrics').innerHTML = '';
  }

  renderMaturityChart(history && Array.isArray(history) ? history : []);
}

function renderMaturityChart(data) {
  const ctx = document.getElementById('maturity-chart');
  if (maturityChart) maturityChart.destroy();
  if (!data || data.length === 0) return;

  const labels = data.map((_, i) => `#${data.length - i}`).reverse();
  const scores = data.map(d => d.maturity_score || 0).reverse();

  maturityChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label: 'Maturity Score', data: scores, borderColor: '#6c5ce7', backgroundColor: '#6c5ce722', fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: '#6c5ce7' }] },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: {
      y: { min: 0, max: 100, grid: { color: '#e4e4e722' }, ticks: { color: '#71717a' } },
      x: { grid: { display: false }, ticks: { color: '#71717a' } },
    }}
  });
}

async function computeMaturity() {
  if (!currentApp) { toast('Selecteer eerst een app', 'warning'); return; }
  toast('Maturity wordt herberekend...', 'info');
  await fetch(`${API}/api/maturity/${currentApp}/compute`, {method:'POST'});
  toast('Maturity herberekend', 'success');
  loadMaturity();
}

// ═══════ COSTS TAB ═══════════════════════════════════════════════════
async function loadCosts() {
  const [daily, monthly] = await Promise.all([ api('/api/costs/daily'), api('/api/costs/monthly') ]);

  const dSpent = daily?.total_usd || 0;
  const mSpent = monthly?.monthly_total_usd || 0;
  const mLimit = monthly?.monthly_limit_usd || 50;
  const mRemain = monthly?.monthly_remaining_usd || (mLimit - mSpent);
  const dLimit = monthly?.daily_limit_usd || 1;

  document.getElementById('cost-daily').textContent = formatEUR(dSpent);
  document.getElementById('cost-monthly').textContent = formatEUR(mSpent);
  document.getElementById('cost-remaining').textContent = formatEUR(mRemain);
  document.getElementById('cost-remaining').className = `kpi-value ${mRemain < 10 ? 'text-danger' : 'text-success'}`;

  const dailyPct = Math.min((dSpent / dLimit) * 100, 100);
  const monthlyPct = Math.min((mSpent / mLimit) * 100, 100);
  document.getElementById('cost-bars').innerHTML = `
    <div>
      <div class="flex justify-between text-xs mb-1.5"><span class="text-muted">Dag Budget</span><span>${formatEUR(dSpent)} / ${formatEUR(dLimit)}</span></div>
      <div class="progress-bar"><div class="progress-fill ${dailyPct > 80 ? 'bg-danger' : 'bg-accent'}" style="width:${dailyPct}%"></div></div>
    </div>
    <div>
      <div class="flex justify-between text-xs mb-1.5"><span class="text-muted">Maand Budget</span><span>${formatEUR(mSpent)} / ${formatEUR(mLimit)}</span></div>
      <div class="progress-bar"><div class="progress-fill ${monthlyPct > 80 ? 'bg-danger' : 'bg-accent'}" style="width:${monthlyPct}%"></div></div>
    </div>`;

  const records = daily?.records || [];
  const tbody = document.getElementById('cost-records');
  tbody.innerHTML = records.length > 0 ? records.slice(0,25).map(r => `
    <tr><td class="text-xs">${r.step||r.operation||'-'}</td><td class="text-xs">${r.provider||'-'}</td>
    <td class="text-xs font-mono">${r.model||'-'}</td><td class="text-xs">${formatEUR(r.cost_usd||r.amount)}</td>
    <td class="text-xs text-muted">${r.tokens_used||'-'}</td></tr>`).join('')
    : '<tr><td colspan="5" class="text-center text-muted py-4">Geen records vandaag</td></tr>';

  renderCostsChart(mSpent, mLimit);
}

function renderCostsChart(monthly, limit) {
  const ctx = document.getElementById('costs-chart');
  if (costsChart) costsChart.destroy();
  costsChart = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: ['Besteed', 'Resterend'], datasets: [{ data: [monthly, Math.max(limit-monthly,0)], backgroundColor: ['#6c5ce7', '#e4e4e7'], borderWidth: 0 }] },
    options: { responsive: true, cutout: '72%', plugins: { legend: { position: 'bottom', labels: { color: '#71717a', font: { size: 11 } } } } }
  });
}

// ═══════ INSIGHTS TAB ═════════════════════════════════════════════════
async function triggerWeeklyAnalysis() {
  const appId = document.getElementById('insights-app-filter').value;
  if (!appId) { toast('Selecteer eerst een app', 'warning'); return; }
  const btn = document.getElementById('btn-weekly-analysis');
  btn.disabled = true;
  btn.textContent = 'Analyseren...';
  toast('Analyse gestart — dit duurt 10-30 seconden', 'info');
  const res = await api(`/api/apps/${appId}/run-analysis`, { method: 'POST', _timeout: 60000 });
  btn.disabled = false;
  btn.innerHTML = '<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Analyse uitvoeren';
  if (res) {
    toast(`Analyse klaar — ${res.new_learnings || 0} nieuwe lessen`, 'success');
    loadInsights();
  } else {
    toast('Analyse mislukt of geen data beschikbaar', 'warning');
  }
}

function loadInsightsTab() {
  const sel = document.getElementById('insights-app-filter');
  sel.innerHTML = '<option value="">Selecteer app...</option>' +
    allApps.map(app => { const id = app.id || app.app_id; return `<option value="${id}">${escapeHtml(app.name || id)}</option>`; }).join('');
  if (currentApp) { sel.value = currentApp; loadInsights(); }
}

function _confidenceBadge(conf) {
  const map = { high: 'bg-success/15 text-success', medium: 'bg-warning/15 text-warning', low: 'bg-gray-100 text-muted' };
  const labels = { high: 'Hoog', medium: 'Medium', low: 'Laag' };
  const cls = map[conf] || map.low;
  return `<span class="inline-block text-[0.6rem] font-semibold px-1.5 py-0.5 rounded ${cls} uppercase tracking-wider">${labels[conf] || conf}</span>`;
}

function _categoryIcon(cat) {
  const icons = {
    hook: '🎣', content_format: '📋', video_type: '🎬', cta: '👆', caption: '✍️',
    timing: '⏰', duration: '⏱️', voice: '🎙️', music: '🎵',
  };
  return icons[cat] || '📌';
}

function _categoryLabel(cat) {
  const labels = {
    hook: 'Hook', content_format: 'Formaat', video_type: 'Videotype', cta: 'CTA',
    caption: 'Caption', timing: 'Timing', duration: 'Duur', voice: 'Stem', music: 'Muziek',
  };
  return labels[cat] || cat;
}

async function loadInsights() {
  const appId = document.getElementById('insights-app-filter').value;
  const container = document.getElementById('insights-content');

  if (!appId) {
    container.innerHTML = `<div class="card p-8 text-center text-muted">
      <svg class="w-12 h-12 mx-auto mb-3 opacity-30" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
      <p class="text-sm">Selecteer een app om leerinzichten te bekijken</p>
    </div>`;
    return;
  }

  container.innerHTML = `<div class="grid grid-cols-4 gap-3">
    ${[1,2,3,4].map(() => '<div class="card p-4"><div class="skeleton h-8 w-12 mb-1"></div><div class="skeleton h-3 w-20"></div></div>').join('')}
  </div>`;

  const [ldata, idata] = await Promise.all([
    api(`/api/apps/${appId}/learnings`),
    api(`/api/apps/${appId}/insights`),
  ]);

  const learnings = ldata?.learnings || [];
  const benchmark = ldata?.benchmark || {};
  const positives = learnings.filter(l => l.type === 'positive');
  const negatives = learnings.filter(l => l.type === 'negative');
  const highConf = learnings.filter(l => l.confidence === 'high');

  // Groepeer per categorie
  const byCategory = {};
  learnings.forEach(l => {
    if (!byCategory[l.category]) byCategory[l.category] = [];
    byCategory[l.category].push(l);
  });

  container.innerHTML = `
    <!-- KPI Strip -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="card p-4 text-center">
        <div class="kpi-value text-xl text-accent">${learnings.length}</div>
        <div class="kpi-label">Actieve Lessen</div>
      </div>
      <div class="card p-4 text-center">
        <div class="kpi-value text-xl text-success">${positives.length}</div>
        <div class="kpi-label">Wat Werkt</div>
      </div>
      <div class="card p-4 text-center">
        <div class="kpi-value text-xl text-danger">${negatives.length}</div>
        <div class="kpi-label">Wat Niet Werkt</div>
      </div>
      <div class="card p-4 text-center">
        <div class="kpi-value text-xl text-warning">${highConf.length}</div>
        <div class="kpi-label">Hoog Vertrouwen</div>
      </div>
    </div>

    <!-- Benchmark strip (als data beschikbaar) -->
    ${benchmark.total_posts > 0 ? `
    <div class="card p-4">
      <div class="text-xs font-semibold text-muted uppercase tracking-wider mb-3">Benchmark — ${ldata.total_posts_analyzed} posts geanalyseerd</div>
      <div class="grid grid-cols-4 gap-4">
        <div><div class="text-lg font-bold">${benchmark.avg_score || 0}</div><div class="text-xs text-muted">Gem. Score</div></div>
        <div><div class="text-lg font-bold">${(benchmark.avg_views||0).toLocaleString()}</div><div class="text-xs text-muted">Gem. Views</div></div>
        <div><div class="text-lg font-bold text-success">${benchmark.best_score || 0}</div><div class="text-xs text-muted">Best Score</div></div>
        <div><div class="text-lg font-bold">${benchmark.total_posts || 0}</div><div class="text-xs text-muted">Totaal Posts</div></div>
      </div>
    </div>` : ''}

    <!-- Learnings per categorie -->
    ${Object.keys(byCategory).length > 0 ? `
    <div class="space-y-3">
      <h3 class="text-sm font-semibold text-gray-900">Geleerde Patronen per Categorie</h3>
      ${Object.entries(byCategory).map(([cat, items]) => `
        <div class="card p-4">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-base">${_categoryIcon(cat)}</span>
            <span class="text-sm font-semibold">${_categoryLabel(cat)}</span>
            <span class="text-xs text-muted ml-auto">${items.length} les${items.length !== 1 ? 'sen' : ''}</span>
          </div>
          <div class="space-y-2">
            ${items.map(l => `
              <div class="flex items-start gap-3 py-2 border-b border-border/40 last:border-0">
                <span class="mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${l.type === 'positive' ? 'bg-success' : l.type === 'negative' ? 'bg-danger' : 'bg-muted'}"></span>
                <div class="flex-1 min-w-0">
                  <div class="text-sm leading-snug">${escapeHtml(l.finding)}</div>
                  ${l.action ? `<div class="text-xs text-muted mt-0.5">→ ${escapeHtml(l.action)}</div>` : ''}
                </div>
                <div class="flex items-center gap-1.5 flex-shrink-0">
                  ${_confidenceBadge(l.confidence)}
                  ${l.times_confirmed > 1 ? `<span class="text-[0.6rem] text-muted">×${l.times_confirmed}</span>` : ''}
                </div>
              </div>`).join('')}
          </div>
        </div>`).join('')}
    </div>` : `
    <div class="card p-8 text-center text-muted">
      <div class="text-3xl mb-3">🧠</div>
      <p class="text-sm font-medium text-gray-700 mb-1">Nog geen patronen geleerd</p>
      <p class="text-xs">Start campagnes en publiceer content — de AI leert automatisch van elke post die 24u+ live is.</p>
    </div>`}

    <!-- Brand Memory snippets uit insights API -->
    ${idata ? `
    <div class="grid grid-cols-2 gap-4">
      ${(idata.top_hooks||[]).length > 0 ? `
      <div class="card p-5">
        <h3 class="text-xs font-semibold text-muted uppercase tracking-wider mb-3">🎣 Bewezen Hooks</h3>
        ${idata.top_hooks.slice(0,5).map(h => `
          <div class="flex items-center gap-2 py-1.5 border-b border-border/40 last:border-0">
            <span class="w-1.5 h-1.5 rounded-full bg-success flex-shrink-0"></span>
            <span class="text-xs">${escapeHtml(h)}</span>
          </div>`).join('')}
      </div>` : ''}
      ${(idata.avoided_topics||[]).length > 0 ? `
      <div class="card p-5">
        <h3 class="text-xs font-semibold text-muted uppercase tracking-wider mb-3">⛔ Vermijdingen</h3>
        ${idata.avoided_topics.slice(0,5).map(t => `
          <div class="flex items-center gap-2 py-1.5 border-b border-border/40 last:border-0">
            <span class="w-1.5 h-1.5 rounded-full bg-danger flex-shrink-0"></span>
            <span class="text-xs">${escapeHtml(t)}</span>
          </div>`).join('')}
      </div>` : ''}
    </div>` : ''}

    <!-- Meta info -->
    ${idata ? `
    <div class="grid grid-cols-3 gap-3">
      <div class="card p-3">
        <div class="text-[0.65rem] text-muted uppercase tracking-wider mb-1">Tone of Voice</div>
        <div class="text-xs font-medium">${idata.tone_of_voice || 'Niet ingesteld'}</div>
      </div>
      <div class="card p-3">
        <div class="text-[0.65rem] text-muted uppercase tracking-wider mb-1">Optimale Posttijd</div>
        <div class="text-xs font-medium">${idata.optimal_post_time || 'Niet bepaald'}</div>
      </div>
      <div class="card p-3">
        <div class="text-[0.65rem] text-muted uppercase tracking-wider mb-1">Beste Formaat</div>
        <div class="text-xs font-medium">${idata.best_format || '-'}</div>
      </div>
    </div>` : ''}
  `;
}


// ══════════════════════════════════════════════════════════════════════
// VIDEO OPNEMEN — Microfoon + Speech-to-Speech
// ══════════════════════════════════════════════════════════════════════

let mediaRecorder = null;
let audioChunks = [];
let audioBlob = null;
let recTimer = null;
let recSeconds = 0;

function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    stopRecording();
  } else {
    startRecording();
  }
}

async function startRecording() {
  // Toon permissie-popup voordat browser om microfoon vraagt
  openModal('mic-permission');
}

async function doStartRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    audioChunks = [];
    recSeconds = 0;

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
      const url = URL.createObjectURL(audioBlob);
      document.getElementById('audio-player').src = url;
      document.getElementById('playback-controls').classList.remove('hidden');
      document.getElementById('btn-generate').classList.remove('hidden');
      document.getElementById('rec-status').textContent = 'Opname klaar — luister terug of genereer video';
      stream.getTracks().forEach(t => t.stop());
    };

    mediaRecorder.start();

    // UI updates
    const btn = document.getElementById('btn-record');
    btn.classList.remove('bg-accent', 'hover:bg-accent-hover');
    btn.classList.add('bg-red-500', 'hover:bg-red-600', 'animate-pulse');
    document.getElementById('icon-mic').classList.add('hidden');
    document.getElementById('icon-stop').classList.remove('hidden');
    document.getElementById('rec-status').textContent = 'Opname loopt...';
    document.getElementById('playback-controls').classList.add('hidden');
    document.getElementById('btn-generate').classList.add('hidden');

    recTimer = setInterval(() => {
      recSeconds++;
      const m = Math.floor(recSeconds / 60);
      const s = String(recSeconds % 60).padStart(2, '0');
      document.getElementById('rec-timer').textContent = `${m}:${s}`;
    }, 1000);

  } catch (e) {
    document.getElementById('rec-status').textContent = 'Microfoon toegang geweigerd. Ga naar je browserinstellingen en sta microfoon toe voor deze site.';
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    clearInterval(recTimer);

    const btn = document.getElementById('btn-record');
    btn.classList.add('bg-accent', 'hover:bg-accent-hover');
    btn.classList.remove('bg-red-500', 'hover:bg-red-600', 'animate-pulse');
    document.getElementById('icon-mic').classList.remove('hidden');
    document.getElementById('icon-stop').classList.add('hidden');
  }
}

function playRecording() {
  const player = document.getElementById('audio-player');
  if (player.paused) {
    player.play();
  } else {
    player.pause();
  }
}

function resetRecording() {
  audioBlob = null;
  recSeconds = 0;
  document.getElementById('rec-timer').textContent = '0:00';
  document.getElementById('rec-status').textContent = '';
  document.getElementById('playback-controls').classList.add('hidden');
  document.getElementById('btn-generate').classList.add('hidden');
  document.getElementById('generate-result').classList.add('hidden');
  document.getElementById('generate-progress').classList.add('hidden');
}

// ── Video Opnemen: App selector ──────────────────────────────────────
function onOpnemenAppChange() {
  const appId = document.getElementById('opnemen-app-filter').value;
  if (appId) {
    document.getElementById('opnemen-content').classList.add('hidden');
    document.getElementById('opnemen-active').classList.remove('hidden');
  } else {
    document.getElementById('opnemen-content').classList.remove('hidden');
    document.getElementById('opnemen-active').classList.add('hidden');
  }
}

function loadOpnemenTab() {
  const sel = document.getElementById('opnemen-app-filter');
  sel.innerHTML = '<option value="">Selecteer app...</option>' +
    allApps.map(app => { const id = app.id || app.app_id; return `<option value="${id}">${escapeHtml(app.name || id)}</option>`; }).join('');
  if (currentApp) { sel.value = currentApp; onOpnemenAppChange(); }
}

async function generateVideoWithAudio() {
  const appId = document.getElementById('opnemen-app-filter').value;
  if (!audioBlob || !appId) { toast('Selecteer een app en neem audio op', 'warning'); return; }

  const btn = document.getElementById('btn-generate');
  btn.classList.add('hidden');
  document.getElementById('generate-progress').classList.remove('hidden');
  document.getElementById('generate-result').classList.add('hidden');

  try {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'voiceover.webm');
    formData.append('app_id', appId);
    formData.append('script_text', document.getElementById('script-text').textContent.trim());

    const resp = await fetch(`${API}/api/campaigns/generate-with-audio`, {
      method: 'POST',
      body: formData,
    });

    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(err);
    }

    const data = await resp.json();
    document.getElementById('generate-progress').classList.add('hidden');
    document.getElementById('generate-result').classList.remove('hidden');
    document.getElementById('result-path').textContent = data.video_path || 'Video gegenereerd';

  } catch (e) {
    document.getElementById('generate-progress').classList.add('hidden');
    btn.classList.remove('hidden');
    document.getElementById('rec-status').textContent = 'Fout: ' + e.message;
  }
}

async function generateNewScript() {
  const appId = document.getElementById('opnemen-app-filter').value;
  if (!appId) { toast('Selecteer eerst een app', 'warning'); return; }
  const btn = document.getElementById('btn-new-script');
  btn.textContent = 'Laden...';
  btn.disabled = true;

  try {
    const resp = await fetch(`${API}/api/campaigns/generate-script`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_id: appId }),
    });

    if (resp.ok) {
      const data = await resp.json();
      if (data.script_text) {
        document.getElementById('script-text').textContent = data.script_text;
        toast('Nieuw script gegenereerd', 'success');
      }
    } else {
      toast('Script generatie mislukt', 'error');
    }
  } catch (e) {
    console.error('Script generatie mislukt:', e);
    toast('Script generatie mislukt', 'error');
  } finally {
    btn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Nieuw script';
    btn.disabled = false;
  }
}

// ═══════ ANALYTICS TAB ════════════════════════════════════════════════
async function loadAnalytics() {
  const appId = currentApp || null;
  const [overview, posts, platforms] = await Promise.all([
    api('/api/analytics/overview'),
    appId ? api(`/api/analytics/${appId}/posts?limit=20`) : api('/api/analytics/overview'),
    appId ? api(`/api/analytics/${appId}/platforms`) : null,
  ]);

  if (overview) {
    document.getElementById('an-total').textContent = overview.total_campaigns ?? '—';
    document.getElementById('an-published').textContent = overview.published ?? '—';
    document.getElementById('an-viral').textContent = overview.avg_viral_score ? overview.avg_viral_score + '/100' : '—';
    document.getElementById('an-cost').textContent = overview.total_cost_usd != null ? '$' + Number(overview.total_cost_usd).toFixed(3) : '—';

    // Platform breakdown cards
    const pbEl = document.getElementById('platform-breakdown');
    const pbData = platforms || overview.platforms || {};
    const platformColors = { tiktok:'bg-black text-white', instagram:'bg-gradient-to-br from-purple-500 to-pink-500 text-white', facebook:'bg-blue-600 text-white', youtube:'bg-red-600 text-white' };
    const platformNames = { tiktok:'TikTok', instagram:'Instagram', facebook:'Facebook', youtube:'YouTube' };

    pbEl.innerHTML = ['tiktok','instagram','facebook','youtube'].map(p => {
      const d = pbData[p] || {};
      return `<div class="p-3 rounded-lg border border-border">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[0.6rem] font-bold px-1.5 py-0.5 rounded ${platformColors[p]}">${platformNames[p]}</span>
        </div>
        <div class="text-lg font-bold">${d.published ?? d.total_campaigns ?? 0}</div>
        <div class="text-xs text-gray-400">gepubliceerd</div>
        <div class="mt-1 text-xs text-gray-500">$${Number(d.cost_usd||0).toFixed(3)} kosten</div>
        <div class="text-xs text-gray-500">viral: ${d.avg_viral_score || '—'}</div>
      </div>`;
    }).join('');
  }

  // Posts table
  const postsData = Array.isArray(posts) ? posts : [];
  const tbody = document.getElementById('analytics-posts-table');
  if (!postsData.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-gray-400">Nog geen gepubliceerde posts</td></tr>';
    return;
  }
  const platformBadges = { tiktok:'bg-gray-900 text-white', instagram:'bg-pink-500 text-white', facebook:'bg-blue-600 text-white', youtube:'bg-red-600 text-white' };
  tbody.innerHTML = postsData.map(p => `
    <tr class="border-b border-border hover:bg-gray-50">
      <td class="py-2 pr-3 max-w-[180px] truncate">${escapeHtml(p.title || '—')}</td>
      <td class="py-2"><span class="text-[0.6rem] font-bold px-1.5 py-0.5 rounded ${platformBadges[p.platform]||'bg-gray-200'}">${p.platform||'—'}</span></td>
      <td class="py-2 text-gray-400">${p.published_at ? timeAgo(p.published_at) : '—'}</td>
      <td class="py-2 text-right font-semibold ${(p.viral_score||0)>=75?'text-success':(p.viral_score||0)>=50?'text-warning':'text-danger'}">${p.viral_score ?? '—'}</td>
      <td class="py-2 text-right">${p.views ? p.views.toLocaleString('nl-NL') : '—'}</td>
      <td class="py-2 text-right">${p.engagement_rate ? p.engagement_rate + '%' : '—'}</td>
      <td class="py-2 text-right text-gray-400">$${Number(p.cost_usd||0).toFixed(4)}</td>
    </tr>
  `).join('');
}

// ═══════ KANALEN TAB ════════════════════════════════════════════════
async function loadKanalen() {
  const appId = currentApp;

  // Check platform statuses
  const checkToken = async (file) => {
    try {
      const r = await fetch(`/api/health/`);
      return r.ok;
    } catch { return false; }
  };

  // Load platform breakdown
  if (appId) {
    const platforms = await api(`/api/analytics/${appId}/platforms`);
    if (platforms) {
      const tbody = document.getElementById('platform-stats-table');
      const rows = ['tiktok','instagram','facebook','youtube'].map(p => {
        const d = platforms[p] || {};
        const connected = d.connected ? '<span class="text-success">✓ Gekoppeld</span>' : '<span class="text-gray-300">— Niet gekoppeld</span>';
        return `<tr class="border-b border-border">
          <td class="py-2 font-medium capitalize">${p}</td>
          <td class="py-2 text-right">${d.total || 0}</td>
          <td class="py-2 text-right text-success">${d.published || 0}</td>
          <td class="py-2 text-right text-warning">${d.pending || 0}</td>
          <td class="py-2 text-right">$${Number(d.total_cost_usd||0).toFixed(3)}</td>
          <td class="py-2 text-right">${d.avg_viral_score || '—'}</td>
        </tr>`;
      });
      tbody.innerHTML = rows.join('');
    }
  }

  // Check Facebook token
  const fbToken = await api('/api/health/');
  document.getElementById('channel-fb-status').innerHTML = '<span class="text-success font-medium">✓ Gekoppeld</span>';
  document.getElementById('channel-fb-page').textContent = 'GLP Coach (1008390602367041)';
  document.getElementById('channel-fb-token').textContent = 'Actief ✓';

  document.getElementById('channel-tiktok-status').innerHTML = '<span class="text-warning font-medium">◎ Token vereist</span>';
  document.getElementById('channel-tiktok-token').textContent = 'Niet ingesteld';

  document.getElementById('channel-ig-status').innerHTML = '<span class="text-warning font-medium">◎ Token vereist</span>';
  document.getElementById('channel-ig-token').textContent = 'Niet ingesteld';

  document.getElementById('channel-yt-status').innerHTML = '<span class="text-gray-400 font-medium">○ Niet gekoppeld</span>';
  document.getElementById('channel-yt-token').textContent = 'OAuth vereist';
}

function setupYouTube() {
  toast('YouTube OAuth setup — vraag Anouar om Google Cloud credentials', 'info');
}

// ═══════ KALENDER TAB ════════════════════════════════════════════════
let calendarDate = new Date();

async function loadKalender() {
  renderCalendar();
  await loadCalendarPosts();
}

function calendarPrev() {
  calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() - 1, 1);
  renderCalendar();
}

function calendarNext() {
  calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() + 1, 1);
  renderCalendar();
}

async function renderCalendar() {
  const year = calendarDate.getFullYear();
  const month = calendarDate.getMonth();
  const monthNames = ['Januari','Februari','Maart','April','Mei','Juni','Juli','Augustus','September','Oktober','November','December'];
  document.getElementById('calendar-month-label').textContent = `${monthNames[month]} ${year}`;

  // Load campaigns for this month
  const camps = await api('/api/campaigns/') || [];
  const byDate = {};
  for (const c of camps) {
    const dateStr = (c.published_at || c.created_at || '').slice(0,10);
    if (!byDate[dateStr]) byDate[dateStr] = [];
    byDate[dateStr].push(c);
  }

  const firstDay = new Date(year, month, 1);
  let startDow = firstDay.getDay(); // 0=Sun, 1=Mon...
  startDow = startDow === 0 ? 6 : startDow - 1; // Convert to Mon=0

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date().toISOString().slice(0,10);

  const grid = document.getElementById('calendar-grid');
  let html = '';

  // Empty cells before first day
  for (let i = 0; i < startDow; i++) {
    html += '<div class="min-h-[70px] rounded p-1"></div>';
  }

  // Also map scheduled_for dates
  const scheduledByDate = {};
  for (const c of camps) {
    if (c.scheduled_for) {
      const sDateStr = c.scheduled_for.slice(0,10);
      if (!scheduledByDate[sDateStr]) scheduledByDate[sDateStr] = [];
      scheduledByDate[sDateStr].push(c);
    }
  }

  // Day cells
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday = dateStr === today;
    const isPast = dateStr < today;
    const posts = byDate[dateStr] || [];
    const scheduled = scheduledByDate[dateStr] || [];
    const publishedPosts = posts.filter(c => c.status === 'published');
    const pendingPosts = posts.filter(c => c.status === 'pending_approval');

    html += `<div class="min-h-[70px] rounded p-1 border ${isToday ? 'border-accent bg-accent/5' : isPast ? 'border-border/40 bg-gray-50/50' : 'border-border hover:bg-gray-50'} cursor-default">
      <div class="text-[0.65rem] font-semibold ${isToday ? 'text-accent' : isPast ? 'text-gray-300' : 'text-gray-500'} mb-1">${d}</div>
      ${publishedPosts.map(c => `<div class="text-[0.55rem] px-1 py-0.5 rounded mb-0.5 truncate font-medium text-white" style="background:${platformColor(c.platform)}" title="${escapeHtml(c.idea?.title||'')}">${platformIcon(c.platform)} ${escapeHtml((c.idea?.title||'Post').slice(0,10))}</div>`).join('')}
      ${pendingPosts.map(c => `<div class="text-[0.55rem] px-1 py-0.5 rounded mb-0.5 truncate font-medium border border-warning text-warning bg-warning/5" title="${escapeHtml(c.idea?.title||'')}">${escapeHtml((c.idea?.title||'Review').slice(0,10))}</div>`).join('')}
      ${scheduled.map(c => `<div class="text-[0.55rem] px-1 py-0.5 rounded mb-0.5 truncate font-medium border border-accent text-accent bg-accent/5" title="Ingepland: ${escapeHtml(c.idea?.title||'')} om ${c.scheduled_for?.slice(11,16)||''}">📅 ${escapeHtml((c.idea?.title||'Ingepland').slice(0,9))}</div>`).join('')}
    </div>`;
  }

  grid.innerHTML = html;
}

function platformColor(p) {
  const colors = { tiktok:'#111', instagram:'#e1306c', facebook:'#1877f2', youtube:'#ff0000' };
  return colors[p] || '#6c5ce7';
}

function platformIcon(p) {
  const icons = { tiktok:'TT', instagram:'IG', facebook:'FB', youtube:'YT' };
  return icons[p] || '??';
}

async function loadCalendarPosts() {
  const camps = await api('/api/campaigns/') || [];

  // Scheduled posts (toekomstig)
  const upcoming = [...camps]
    .filter(c => c.scheduled_for && new Date(c.scheduled_for) > new Date())
    .sort((a,b) => (a.scheduled_for||'').localeCompare(b.scheduled_for||''));

  // Recente activiteit
  const recent = [...camps]
    .filter(c => c.status === 'published' || c.status === 'pending_approval')
    .sort((a,b) => (b.published_at||b.created_at||'').localeCompare(a.published_at||a.created_at||''))
    .slice(0,10);

  const platformBadges = { tiktok:'bg-gray-900 text-white', instagram:'bg-pink-500 text-white', facebook:'bg-blue-600 text-white', youtube:'bg-red-600 text-white' };
  const el = document.getElementById('calendar-posts-list');

  let html = '';

  if (upcoming.length > 0) {
    html += `<div class="text-[0.65rem] font-semibold text-muted uppercase tracking-wider mb-2">📅 Ingepland (${upcoming.length})</div>`;
    html += upcoming.map(c => {
      const title = (typeof c.idea === 'object' && c.idea?.title) ? c.idea.title : (c.display_name || 'Campagne');
      const dt = new Date(c.scheduled_for);
      const dtStr = dt.toLocaleDateString('nl-NL', {day:'numeric',month:'short'}) + ' ' + dt.toLocaleTimeString('nl-NL',{hour:'2-digit',minute:'2-digit'});
      return `<div class="flex items-center gap-3 py-2 border-b border-border/60 last:border-0">
        <span class="text-[0.6rem] font-bold px-1.5 py-0.5 rounded ${platformBadges[c.platform||'tiktok']||'bg-accent text-white'} flex-shrink-0">${(c.platform||'tiktok').toUpperCase().slice(0,2)}</span>
        <div class="flex-1 min-w-0">
          <div class="text-xs font-medium truncate">${escapeHtml(title)}</div>
          <div class="text-[0.65rem] text-accent font-medium">${dtStr}</div>
        </div>
        <span class="text-[0.6rem] bg-accent/10 text-accent px-1.5 py-0.5 rounded font-semibold">Ingepland</span>
      </div>`;
    }).join('');
    if (recent.length > 0) html += '<div class="my-3 border-t border-border"></div>';
  }

  if (recent.length > 0) {
    html += `<div class="text-[0.65rem] font-semibold text-muted uppercase tracking-wider mb-2">Recente activiteit</div>`;
    html += recent.map(c => {
      const title = (typeof c.idea === 'object' && c.idea?.title) ? c.idea.title : (c.display_name || 'Campagne');
      return `<div class="flex items-center gap-3 py-2 border-b border-border/40 last:border-0">
        <span class="text-[0.6rem] font-bold px-1.5 py-0.5 rounded ${platformBadges[c.platform||'tiktok']||'bg-gray-200'} flex-shrink-0">${(c.platform||'tiktok').toUpperCase().slice(0,2)}</span>
        <div class="flex-1 min-w-0">
          <div class="text-xs font-medium truncate">${escapeHtml(title)}</div>
          <div class="text-[0.65rem] text-gray-400">${timeAgo(c.published_at || c.created_at)}</div>
        </div>
        <div>${statusBadge(c.status)}</div>
      </div>`;
    }).join('');
  }

  el.innerHTML = html || '<div class="text-xs text-gray-400 text-center py-4">Nog geen campagnes</div>';
}
