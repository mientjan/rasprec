// Vanilla, no build step. Live view iframes MediaMTX's own HLS player through
// the proxy, so no player library has to be vendored.

const $ = (sel) => document.querySelector(sel);
const api = async (url) => {
  const res = await fetch(url);
  if (res.status === 401) { location.assign('/login'); throw new Error('Please sign in'); }
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
};

const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();
const fmtDuration = (s) => (s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);

let session = {};
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const notice = (text) => { $('#notice').textContent = text; };
async function mutate(url, data = {}) {
  const res = await fetch(url, {method:'POST', headers:{
    'Content-Type':'application/json', 'X-CSRF-Token':session.csrf || '', 'X-Capture-Request':'1'
  }, body:JSON.stringify(data)});
  if(res.status === 401) { location.assign('/login'); throw new Error('Please sign in'); }
  const body = await res.json();
  if(!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed');
  return body;
}
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
        <div class="capture-actions">
          <button data-camera="${cam.name}" data-capture="photo" ${cam.detecting ? '' : 'disabled'}>Take photo</button>
          <button data-camera="${cam.name}" data-capture="clip" ${cam.detecting && cam.recording ? '' : 'disabled'}>Record 15-second clip</button>
          <small>Recording configured: ${cam.recording ? 'yes' : 'no'}</small>
        </div>
      </div>`
    )
    .join('');
}

async function refreshStatus() {
  try {
    const next = await api('/api/cameras');
    const changed = next.length !== cameras.length ||
      next.some((c, i) => c.detecting !== cameras[i]?.detecting || c.recording !== cameras[i]?.recording);
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
    <div class="card ${ev.clip || ev.image ? 'clickable' : ''}" data-id="${ev.id}" data-camera="${ev.camera}" data-start="${ev.start_ts}" data-kind="${ev.kind}" data-image="${ev.image ? 1 : 0}" data-clip="${ev.clip ? 1 : 0}">
      ${thumb}
      <div class="meta">
        <strong>${escapeHTML(ev.camera)} · ${escapeHTML(ev.source)}</strong>
        <span class="muted">${fmtTime(ev.start_ts)} &middot; ${fmtDuration(ev.duration)}</span>
        <span>${escapeHTML(ev.status)}${ev.error ? ': ' + escapeHTML(ev.error) : ''}</span>
      </div>
    </div>`;
}

function eventQuery(beforeId) {
  const params = new URLSearchParams({ limit: '48' });
  if ($('#ev-camera').value) params.set('camera', $('#ev-camera').value);
  if ($('#ev-date').value) {
    const start = new Date($('#ev-date').value + 'T00:00:00');
    const end = new Date(start); end.setDate(end.getDate() + 1);
    params.set('since', start.getTime() / 1000); params.set('until', end.getTime() / 1000);
  }
  if ($('#ev-source').value) params.set('source', $('#ev-source').value);
  if ($('#ev-kind').value) params.set('kind', $('#ev-kind').value);
  if (beforeId) params.set('before_id', beforeId);
  return `/api/events?${params}`;
}

async function loadEvents(append = false) {
  const events = await api(eventQuery(append ? oldestEventId : null));
  if (!append) { $('#ev-grid').innerHTML = ''; oldestEventId = null; }
  $('#ev-grid').insertAdjacentHTML('beforeend', events.map(eventCard).join(''));
  oldestEventId = events.length ? events[events.length - 1].id : oldestEventId;
  $('#ev-more').hidden = events.length < 48;
  $('#ev-empty').hidden = !!$('#ev-grid').children.length;
}

$('#ev-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.card.clickable');
  if (!card) return;
  const photo = card.dataset.clip !== '1';
  const src = `/media/${photo ? 'image' : 'clip'}/${card.dataset.id}`;
  openModal(src, `${card.dataset.camera} — ${fmtTime(Number(card.dataset.start))}`, photo);
  $('#modal-photo').hidden = photo || card.dataset.image !== '1';
  $('#modal-photo').href = `/media/image/${card.dataset.id}`;
});

$('#ev-reload').addEventListener('click', () => loadEvents());
$('#ev-camera').addEventListener('change', () => loadEvents());
$('#ev-date').addEventListener('change', () => loadEvents());
$('#ev-source').addEventListener('change', () => loadEvents());
$('#ev-kind').addEventListener('change', () => loadEvents());
$('#ev-clear').addEventListener('click', () => {
  $('#ev-camera').value = '';
  $('#ev-date').value = '';
  $('#ev-source').value = ''; $('#ev-kind').value = '';
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

function openModal(src, label, photo = false) {
  $('#modal-video').hidden = photo; $('#modal-image').hidden = !photo;
  if (photo) $('#modal-image').src = src; else $('#modal-video').src = src;
  $('#modal-download').href = src + (photo ? '?download=true' : '');
  $('#modal-download').download = photo ? 'capture.jpg' : 'capture.mp4';
  $('#modal-label').textContent = label;
  $('#modal').hidden = false;
}

function closeModal() {
  $('#modal-video').pause();
  $('#modal-video').removeAttribute('src');
  $('#modal-image').removeAttribute('src');
  $('#modal').hidden = true;
}

$('#modal-close').addEventListener('click', closeModal);
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// --- boot -----------------------------------------------------------------

(async function start() {
  closeModal();
  session = await api('/api/session');
  $('#logout').hidden = session.mode !== 'session';
  cameras = await api('/api/cameras');
  renderLive();
  const options = cameras.map((c) => `<option value="${c.name}">${c.name}</option>`).join('');
  $('#ev-camera').insertAdjacentHTML('beforeend', options);
  $('#ar-camera').innerHTML = options;
  await loadEvents();
  await refreshStatus();
  setInterval(refreshStatus, 10000);
  setInterval(() => { if ($('#events').classList.contains('active')) loadEvents(); }, 30000);
})().catch(err => notice(err.message));

$('#live-grid').addEventListener('click', async (e) => {
  const button = e.target.closest('button[data-capture]');
  if (!button) return;
  button.disabled = true;
  try {
    const result = await mutate(`/api/cameras/${encodeURIComponent(button.dataset.camera)}/captures`, {kind:button.dataset.capture});
    notice(`Capture #${result.id} queued. Check Events for the result.`);
    await loadEvents();
  } catch (err) { notice(err.message); }
  finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  try { await mutate('/logout'); location.assign('/login'); }
  catch(err) { notice(err.message); }
});
window.addEventListener('unhandledrejection', e => { notice(e.reason?.message || 'Request failed'); });
