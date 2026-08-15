// Vanilla, no build step. Live view iframes MediaMTX's own HLS player through
// the proxy, so no player library has to be vendored.

const $ = (sel) => document.querySelector(sel);
const api = async (url) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
};

const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();
const fmtDuration = (s) => (s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);

let cameras = [];
let oldestEventId = null;

// --- navigation -----------------------------------------------------------

document.querySelectorAll('nav button').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach((b) => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === btn.dataset.view));
    if (btn.dataset.view === 'archive') loadSegments();
  });
});

// --- live -----------------------------------------------------------------

function renderLive() {
  $('#live-grid').innerHTML = cameras
    .map(
      (cam) => `
      <div class="card">
        <iframe src="/hls/${encodeURIComponent(cam.name)}/" allow="autoplay; fullscreen" title="${cam.name}"></iframe>
        <div class="meta">
          <strong>${cam.name}</strong>
          <span><span class="dot ${cam.detecting ? 'on' : ''}"></span> ${cam.detecting ? 'detecting' : 'no signal'}</span>
        </div>
      </div>`
    )
    .join('');
}

async function refreshStatus() {
  try {
    const next = await api('/api/cameras');
    const changed = next.length !== cameras.length ||
      next.some((c, i) => c.detecting !== cameras[i]?.detecting);
    cameras = next;
    if (changed || !$('#live-grid').children.length) renderLive();
    const up = cameras.filter((c) => c.detecting).length;
    $('#status').textContent = `${up}/${cameras.length} cameras up`;
  } catch (err) {
    $('#status').textContent = 'connection lost';
  }
}

// --- events ---------------------------------------------------------------

function eventCard(ev) {
  const thumb = ev.thumb
    ? `<img src="/media/thumb/${ev.id}" loading="lazy" alt="">`
    : `<img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" alt="">`;
  return `
    <div class="card ${ev.clip ? 'clickable' : ''}" data-id="${ev.id}" data-camera="${ev.camera}" data-start="${ev.start_ts}">
      ${thumb}
      <div class="meta">
        <strong>${ev.camera}</strong>
        <span class="muted">${fmtTime(ev.start_ts)} &middot; ${fmtDuration(ev.duration)}</span>
      </div>
    </div>`;
}

function eventQuery(beforeId) {
  const params = new URLSearchParams({ limit: '48' });
  if ($('#ev-camera').value) params.set('camera', $('#ev-camera').value);
  if ($('#ev-date').value) params.set('date', $('#ev-date').value);
  if (beforeId) params.set('before_id', beforeId);
  return `/api/events?${params}`;
}

async function loadEvents(append = false) {
  const events = await api(eventQuery(append ? oldestEventId : null));
  if (!append) $('#ev-grid').innerHTML = '';
  $('#ev-grid').insertAdjacentHTML('beforeend', events.map(eventCard).join(''));
  oldestEventId = events.length ? events[events.length - 1].id : oldestEventId;
  $('#ev-more').hidden = events.length < 48;
  $('#ev-empty').hidden = !!$('#ev-grid').children.length;
}

$('#ev-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.card.clickable');
  if (!card) return;
  openModal(`/media/clip/${card.dataset.id}`, `${card.dataset.camera} — ${fmtTime(Number(card.dataset.start))}`);
});

$('#ev-reload').addEventListener('click', () => loadEvents());
$('#ev-camera').addEventListener('change', () => loadEvents());
$('#ev-date').addEventListener('change', () => loadEvents());
$('#ev-clear').addEventListener('click', () => {
  $('#ev-camera').value = '';
  $('#ev-date').value = '';
  loadEvents();
});
$('#ev-more').addEventListener('click', () => loadEvents(true));

// --- archive --------------------------------------------------------------

async function loadSegments() {
  const camera = $('#ar-camera').value;
  if (!camera) return;
  const segments = await api(`/api/timeline?camera=${encodeURIComponent(camera)}`);
  if (!segments.length) {
    $('#ar-list').innerHTML = '<li class="muted">No recordings yet.</li>';
    return;
  }
  $('#ar-list').innerHTML = segments
    .slice()
    .reverse()
    .map(
      (seg) => `<li data-start="${seg.start}" data-duration="${seg.duration}">
          ${new Date(seg.start).toLocaleString()}<br>
          <span class="muted">${fmtDuration(seg.duration)}</span>
        </li>`
    )
    .join('');
}

$('#ar-list').addEventListener('click', (e) => {
  const item = e.target.closest('li[data-start]');
  if (!item) return;
  const camera = $('#ar-camera').value;
  // Cap a playback request so a full-length segment never becomes a huge cut.
  const duration = Math.min(Number(item.dataset.duration), 900);
  const params = new URLSearchParams({ camera, start: item.dataset.start, duration });
  $('#ar-video').src = `/archive.mp4?${params}`;
  $('#ar-video').play().catch(() => {});
  $('#ar-label').textContent = `${camera} from ${new Date(item.dataset.start).toLocaleString()}`;
});

$('#ar-reload').addEventListener('click', loadSegments);
$('#ar-camera').addEventListener('change', loadSegments);

// --- modal ----------------------------------------------------------------

function openModal(src, label) {
  $('#modal-video').src = src;
  $('#modal-label').textContent = label;
  $('#modal').hidden = false;
}

function closeModal() {
  $('#modal-video').pause();
  $('#modal-video').removeAttribute('src');
  $('#modal').hidden = true;
}

$('#modal-close').addEventListener('click', closeModal);
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// --- boot -----------------------------------------------------------------

(async function start() {
  closeModal();
  cameras = await api('/api/cameras');
  renderLive();
  const options = cameras.map((c) => `<option value="${c.name}">${c.name}</option>`).join('');
  $('#ev-camera').insertAdjacentHTML('beforeend', options);
  $('#ar-camera').innerHTML = options;
  await loadEvents();
  await refreshStatus();
  setInterval(refreshStatus, 10000);
  setInterval(() => { if ($('#events').classList.contains('active')) loadEvents(); }, 30000);
})();
