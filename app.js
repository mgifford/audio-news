// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Local State Controller
 * Manages local preferences, client-side RSS fetching, and Web Speech API playback.
 *
 * Design boundaries (see PHASE0.md):
 * - Extractive only: the deck shows and reads the feed's own title and summary. No text is invented.
 * - Link lineage: source links come straight from the feed and are attached by this UI, never rewritten.
 * - Transcript parity: the text read aloud is exactly the text shown on the card.
 * - No unsafe rendering: feed content is inserted with textContent, never innerHTML.
 */

// True only in Microsoft Edge, where the `read:` protocol opens Immersive Reader.
// In other browsers that protocol does nothing, so the affordance is Edge-only.
const IS_EDGE = /\bEdg\//.test(navigator.userAgent);

// The visible summary and the spoken summary use the same capped string, so audio matches transcript.
const MAX_DESC = 320;

// History cap: number of recently-read story links remembered locally.
const MAX_HISTORY = 50;

// Use the pre-fetched cache only if it was generated within this window; otherwise
// fall back to the live proxy so a stale baked cache never serves old news.
const CACHE_MAX_AGE_MS = 12 * 60 * 60 * 1000;

const APP_STATE = {
  theme: localStorage.getItem('sojo_theme') || 'dark',
  ratios: readJSON('sojo_ratios', { local: 1, regional: 1, national: 2, international: 1 }),
  beats: readJSON('sojo_beats', []),
  region: localStorage.getItem('sojo_region') || 'ottawa',
  lang: localStorage.getItem('sojo_lang') || '',   // '' = follow the region's language
  regions: [],
  history: readJSON('sojo_history', []),
  currentDeck: [],
  sources: null,
  cache: null
};

// Effective content language: an explicit user choice, else the region's language.
function activeLang() {
  if (APP_STATE.lang) return APP_STATE.lang;
  const r = APP_STATE.regions.find(x => x.id === APP_STATE.region);
  return (r && r.language) || (APP_STATE.cache && APP_STATE.cache.language) || 'en';
}

// Client-side CORS proxy so a static page can read cross-origin RSS.
// A third party sees which feeds load; the privacy-preserving alternative is the
// Phase 0 server-side pre-fetch. Overridable at runtime via localStorage 'sojo_proxy'.
const CORS_PROXY = localStorage.getItem('sojo_proxy') || 'https://api.allorigins.win/raw?url=';

// Phase 2 backend base URL. Default '' means same-origin: when this page is served
// by the FastAPI Docker Space, /api/generate-bulletin hits the local backend. On a
// static host with no backend the call fails and we fall back to the local reader.
// Override for a cross-origin backend via localStorage 'sojo_api'.
const API_BASE = localStorage.getItem('sojo_api') ?? '';

const SLIDER_KEYS = [
  { ui: 'Local', state: 'local' },
  { ui: 'Regional', state: 'regional' },
  { ui: 'National', state: 'national' },
  { ui: 'World', state: 'international' }
];

document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  bindUIEvents();
  await loadFeedRegistry();
});

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function initTheme() {
  document.documentElement.setAttribute('data-theme', APP_STATE.theme);
  const toggle = document.getElementById('themeToggle');
  toggle.setAttribute('aria-pressed', String(APP_STATE.theme === 'high-contrast'));
}

async function loadFeedRegistry() {
  try {
    const res = await fetch('sources.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    APP_STATE.sources = await res.json();
    updateStatus('Feed registry loaded. Ready to build your deck.');
  } catch (err) {
    updateStatus('Could not load sources.json. Make sure the page is served over http(s), not opened as a file.', true);
  }
  await loadRegions();
  await loadFeedCache();
}

// Populate the region picker from the backend (or the static registry as fallback).
async function loadRegions() {
  let regions = [];
  try {
    const res = await fetch(`${API_BASE}/api/regions`, { cache: 'no-store' });
    if (res.ok) regions = (await res.json()).regions || [];
  } catch { /* no backend: derive from sources.json */ }
  if (!regions.length && APP_STATE.sources && APP_STATE.sources.regions) {
    regions = Object.entries(APP_STATE.sources.regions).map(([id, r]) =>
      ({ id, name: r.name || id, country: r.country || '', language: r.language || 'en' }));
  }
  APP_STATE.regions = regions;
  if (regions.length && !regions.some(r => r.id === APP_STATE.region)) {
    APP_STATE.region = regions[0].id;
  }

  const sel = document.getElementById('regionSelect');
  if (sel) {
    sel.textContent = '';
    regions.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.id;
      opt.textContent = r.country ? `${r.name}, ${r.country}` : r.name;
      sel.appendChild(opt);
    });
    sel.value = APP_STATE.region;
  }
  const langSel = document.getElementById('langSelect');
  if (langSel) langSel.value = activeLang();
}

// Prefer server-side feeds so the browser never depends on a third-party CORS proxy:
//   1) backend /api/feeds (same-origin on the Space; fetched server-side, no proxy)
//   2) static feeds-cache.json (kept fresh by the scheduled pre-fetch, e.g. on Pages)
// A miss at both leaves APP_STATE.cache null and the deck builder falls back to the
// live proxy per scope.
async function loadFeedCache() {
  // 1) backend endpoint (bounded: a cold Space builds the cache; don't hang forever)
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 20000);
    let res;
    try {
      res = await fetch(`${API_BASE}/api/feeds?region=${encodeURIComponent(APP_STATE.region)}`, { cache: 'no-store', signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
    if (res.ok) {
      const cache = await res.json();
      if (cacheHasItems(cache)) { APP_STATE.cache = cache; return; }
    }
  } catch { /* no backend / timed out: try the static cache next */ }

  // 2) static pre-fetched cache, only if recent enough
  try {
    const res = await fetch('feeds-cache.json', { cache: 'no-store' });
    if (!res.ok) return;
    const cache = await res.json();
    const ageMs = Date.now() - new Date(cache.generatedAt).getTime();
    if (Number.isFinite(ageMs) && ageMs <= CACHE_MAX_AGE_MS && cacheHasItems(cache)) {
      APP_STATE.cache = cache;
    }
  } catch { /* fall back to the live proxy */ }
}

function cacheHasItems(cache) {
  const geo = cache && cache.geography;
  return !!geo && Object.values(geo).some(list => list.some(s => s.items && s.items.length));
}

function bindUIEvents() {
  // Theme toggle (dark <-> high-contrast).
  document.getElementById('themeToggle').addEventListener('click', () => {
    APP_STATE.theme = APP_STATE.theme === 'dark' ? 'high-contrast' : 'dark';
    try { localStorage.setItem('sojo_theme', APP_STATE.theme); } catch { /* storage may be unavailable */ }
    initTheme();
  });

  // Settings drawer toggle with focus management.
  document.getElementById('settingsToggle').addEventListener('click', toggleDrawer);

  // Close the drawer with Escape when focus is inside it.
  document.getElementById('settingsDrawer').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });

  // Sliders sync to state and their visible value.
  SLIDER_KEYS.forEach(({ ui, state }) => {
    const slider = document.getElementById(`slider${ui}`);
    const valDisplay = document.getElementById(`val${ui}`);
    slider.value = APP_STATE.ratios[state];
    valDisplay.textContent = slider.value;
    slider.addEventListener('input', (e) => {
      valDisplay.textContent = e.target.value;
      APP_STATE.ratios[state] = parseInt(e.target.value, 10);
    });
  });

  // Region: switching reloads that region's feeds and follows its language.
  document.getElementById('regionSelect').addEventListener('change', async (e) => {
    APP_STATE.region = e.target.value;
    try { localStorage.setItem('sojo_region', APP_STATE.region); } catch { /* ignore */ }
    APP_STATE.cache = null;
    const langSel = document.getElementById('langSelect');
    if (!APP_STATE.lang && langSel) langSel.value = activeLang();
    updateStatus('Loading feeds for the new region…');
    await loadFeedCache();
    populateVoiceList();
    updateStatus('Region updated. Select “Fetch fresh deck”.');
  });

  // Language: content-framing + preferred TTS voice.
  document.getElementById('langSelect').addEventListener('change', (e) => {
    APP_STATE.lang = e.target.value;
    try { localStorage.setItem('sojo_lang', APP_STATE.lang); } catch { /* ignore */ }
    populateVoiceList();
  });

  // Topic beats: restore checkboxes from state.
  document.querySelectorAll('.beat-check').forEach(cb => {
    cb.checked = APP_STATE.beats.includes(cb.value);
  });

  // Save settings locally (ratios + beats).
  document.getElementById('saveSettingsBtn').addEventListener('click', () => {
    APP_STATE.beats = Array.from(document.querySelectorAll('.beat-check:checked')).map(cb => cb.value);
    try {
      localStorage.setItem('sojo_ratios', JSON.stringify(APP_STATE.ratios));
      localStorage.setItem('sojo_beats', JSON.stringify(APP_STATE.beats));
    } catch { /* ignore */ }
    closeDrawer();
    updateStatus('Settings saved locally.');
  });

  // Fetch a fresh deck.
  document.getElementById('fetchDeckBtn').addEventListener('click', generateNewsDeck);

  // Audio controls.
  document.getElementById('playAudioBtn').addEventListener('click', readDeckAloud);
  document.getElementById('generateBroadcastBtn').addEventListener('click', () => generateAIBroadcastScript(APP_STATE.currentDeck));
  document.getElementById('stopAudioBtn').addEventListener('click', stopReading);

  // Voice + speed controls (apply to both the local reader and the AI broadcast).
  const speed = document.getElementById('speedRate');
  const storedRate = parseFloat(localStorage.getItem('sojo_rate'));
  if (storedRate >= 0.5 && storedRate <= 2) speed.value = String(storedRate);
  document.getElementById('speedVal').textContent = `${parseFloat(speed.value).toFixed(2)}×`;
  speed.addEventListener('input', () => {
    document.getElementById('speedVal').textContent = `${parseFloat(speed.value).toFixed(2)}×`;
    try { localStorage.setItem('sojo_rate', speed.value); } catch { /* ignore */ }
  });
  document.getElementById('voiceSelect').addEventListener('change', (e) => {
    const v = window.speechSynthesis.getVoices()[e.target.value];
    if (v) { try { localStorage.setItem('sojo_voice', v.name); } catch { /* ignore */ } }
  });

  if ('speechSynthesis' in window) {
    populateVoiceList();
    window.speechSynthesis.onvoiceschanged = populateVoiceList; // voices load asynchronously
  }

  // If the tab is closed or navigated, stop any speech.
  window.addEventListener('beforeunload', () => window.speechSynthesis && window.speechSynthesis.cancel());
}

// Fill the voice dropdown from the system/browser voices. Restores a saved choice,
// otherwise prefers a high-quality Edge "Natural"/"Online" voice when present.
function populateVoiceList() {
  const select = document.getElementById('voiceSelect');
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return;

  const saved = (() => { try { return localStorage.getItem('sojo_voice'); } catch { return null; } })();
  const current = select.value;
  select.textContent = '';

  const lang = activeLang();
  const inLang = (v) => (v.lang || '').toLowerCase().startsWith(lang.toLowerCase());
  let chosenIndex = -1;
  let firstInLang = -1;
  voices.forEach((voice, i) => {
    const option = document.createElement('option');
    option.textContent = `${voice.name} (${voice.lang})`;
    option.value = String(i);
    select.appendChild(option);
    if (firstInLang === -1 && inLang(voice)) firstInLang = i;
    if (saved && voice.name === saved) chosenIndex = i;
    // Prefer a high-quality voice in the active language.
    else if (chosenIndex === -1 && !saved && inLang(voice) && /Natural|Online/.test(voice.name)) chosenIndex = i;
  });

  // Fall back to any voice in the active language, then the prior/first choice.
  if (chosenIndex === -1) chosenIndex = firstInLang;
  if (chosenIndex === -1 && current && voices[current]) chosenIndex = Number(current);
  if (chosenIndex >= 0) select.value = String(chosenIndex);
}

function toggleDrawer() {
  const drawer = document.getElementById('settingsDrawer');
  if (drawer.hasAttribute('hidden')) openDrawer();
  else closeDrawer();
}

function openDrawer() {
  const drawer = document.getElementById('settingsDrawer');
  drawer.hidden = false;
  document.getElementById('settingsToggle').setAttribute('aria-expanded', 'true');
  drawer.focus();
}

function closeDrawer() {
  const drawer = document.getElementById('settingsDrawer');
  if (drawer.hidden) return;
  drawer.hidden = true;
  const toggle = document.getElementById('settingsToggle');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.focus();
}

async function generateNewsDeck() {
  if (!APP_STATE.sources) {
    updateStatus('Feed registry is not loaded yet.', true);
    return;
  }
  stopReading();
  updateStatus('Fetching stories across geographic tiers…');

  const queue = document.getElementById('newsQueue');
  queue.setAttribute('aria-busy', 'true');

  const deck = [];
  const geoCategories = ['local', 'regional', 'national', 'international'];

  const usingCache = !!APP_STATE.cache;

  for (const geo of geoCategories) {
    const targetCount = APP_STATE.ratios[geo];
    if (targetCount <= 0) continue;

    const { source, items } = await pickScopeStories(geo);
    if (!source) continue;

    // Skip stories already in local history.
    const freshItems = items.filter(item => !APP_STATE.history.includes(item.link));

    freshItems.slice(0, targetCount).forEach(item => {
      deck.push({
        scope: geo,
        beat: source.beat || null,
        sourceName: source.name,
        title: item.title,
        description: item.description,
        link: item.link
      });
    });
  }

  // Topic beats: add one specialized story per selected beat.
  for (const beat of APP_STATE.beats) {
    const { source, items } = await pickBeatStories(beat);
    if (!source) continue;
    const fresh = items.filter(item => !APP_STATE.history.includes(item.link));
    if (!fresh.length) continue;
    const item = fresh[0];
    deck.push({
      scope: 'national',   // beats have no geographic scope; the beat drives the transition
      beat,
      sourceName: source.name,
      title: item.title,
      description: item.description,
      link: item.link
    });
  }

  APP_STATE.currentDeck = deck;
  renderQueue(deck);
  queue.setAttribute('aria-busy', 'false');

  const canPlay = deck.length > 0 && 'speechSynthesis' in window;
  document.getElementById('playAudioBtn').disabled = !canPlay;
  document.getElementById('generateBroadcastBtn').disabled = !canPlay;

  if (deck.length > 0) {
    const via = usingCache ? ' (from the pre-fetched cache)' : '';
    updateStatus(`Built a fresh deck with ${deck.length} ${deck.length === 1 ? 'story' : 'stories'}${via}.`);
  } else {
    updateStatus('No new stories found. Try adjusting the sliders, or the feeds may be unreachable from this browser.');
  }
}

// The feed sets for the current region: the server cache when present, else built
// from the static registry for the selected region (live-proxy fallback).
function currentGeography() {
  if (APP_STATE.cache && APP_STATE.cache.geography) return APP_STATE.cache.geography;
  const s = APP_STATE.sources;
  if (!s || !s.regions) return null;
  const r = s.regions[APP_STATE.region] || {};
  return {
    local: r.local || [], regional: r.regional || [], national: r.national || [],
    international: s.international || [], beats: s.beats || []
  };
}

async function _pickFrom(list) {
  if (!list || !list.length) return { source: null, items: [] };
  const withItems = list.filter(s => s.items && s.items.length);
  const pool = withItems.length ? withItems : list;
  const source = pool[Math.floor(Math.random() * pool.length)];
  return { source, items: source.items || await fetchRSSFeed(source) };
}

// Pick a random source for a geographic scope in the current region.
async function pickScopeStories(geo) {
  const g = currentGeography();
  return _pickFrom(g && g[geo]);
}

// Pick a source for a topic beat (shared across regions).
async function pickBeatStories(beat) {
  const g = currentGeography();
  const beats = (g && g.beats) || [];
  return _pickFrom(beats.filter(s => s.beat === beat));
}

function extractLink(item) {
  // Atom: prefer the alternate link; fall back to any link with an href.
  const links = Array.from(item.querySelectorAll('link'));
  const alternate = links.find(l => l.getAttribute('rel') === 'alternate' && l.getAttribute('href'));
  const anyHref = links.find(l => l.getAttribute('href'));
  if (alternate) return alternate.getAttribute('href');
  if (anyHref) return anyHref.getAttribute('href');
  // RSS: <link> holds the URL as text.
  const textLink = links.find(l => l.textContent && l.textContent.trim());
  return textLink ? textLink.textContent.trim() : '';
}

async function fetchRSSFeed(source) {
  try {
    const targetUrl = encodeURIComponent(source.url);
    const res = await fetch(`${CORS_PROXY}${targetUrl}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();

    const xml = new DOMParser().parseFromString(text, 'text/xml');
    if (xml.querySelector('parsererror')) throw new Error('Malformed XML');

    const items = Array.from(xml.querySelectorAll('item, entry'));

    return items.map(item => {
      const title = (item.querySelector('title')?.textContent || 'Untitled').trim();
      const link = extractLink(item);
      const rawDesc = item.querySelector('description, summary, content')?.textContent || '';
      // Strip tags and collapse whitespace so the summary is speech-ready, then cap it.
      const cleanDesc = rawDesc.replace(/<[^>]*>?/gm, ' ').replace(/\s+/g, ' ').trim();
      return { title, link, description: capText(cleanDesc, MAX_DESC) };
    }).filter(item => item.link); // drop items with no verifiable source link
  } catch (err) {
    console.warn(`Failed to fetch feed: ${source.name}`, err);
    return [];
  }
}

function capText(text, max) {
  if (text.length <= max) return text;
  const slice = text.slice(0, max);
  const lastSpace = slice.lastIndexOf(' ');
  return `${(lastSpace > 40 ? slice.slice(0, lastSpace) : slice).trim()}…`;
}

function renderQueue(deck) {
  const container = document.getElementById('newsQueue');
  container.textContent = ''; // clear safely

  if (deck.length === 0) {
    const hint = document.createElement('p');
    hint.className = 'empty-hint';
    hint.textContent = 'No stories in the deck yet. Choose a mix in Deck settings and select “Fetch fresh deck”.';
    container.appendChild(hint);
    return;
  }

  deck.forEach(story => {
    const card = document.createElement('article');
    card.className = 'card news-card';

    const tag = document.createElement('span');
    tag.className = 'scope-tag';
    const label = story.beat ? `${story.beat.toUpperCase()} · ${story.scope.toUpperCase()}` : story.scope.toUpperCase();
    tag.textContent = `${label} — ${story.sourceName}`;

    const heading = document.createElement('h3');
    heading.textContent = story.title;

    const summary = document.createElement('p');
    summary.textContent = story.description;

    const link = sourceLink(story.link, `Verify source at ${story.sourceName}`);

    card.append(tag, heading, summary, link);
    const edge = edgeReaderLink(story.link);
    if (edge) card.appendChild(edge);
    container.appendChild(card);
  });
}

// Phase 1 local reader: reads exactly what is shown on the cards (transcript parity).
function readDeckAloud() {
  if (APP_STATE.currentDeck.length === 0) return;

  let broadcastScript = 'This is your local and global news bulletin. ';
  APP_STATE.currentDeck.forEach(item => {
    broadcastScript += `Turning to ${item.scope} news from ${item.sourceName}. ${item.title}. ${item.description} `;
  });

  recordHistory(APP_STATE.currentDeck);
  clearScript(); // the cards themselves are the transcript in local mode
  speak(broadcastScript, 'Reading the deck aloud…');
}

// Phase 2 broadcast: send the deck to the backend, then read the returned script.
async function generateAIBroadcastScript(deckItems) {
  if (!deckItems || deckItems.length === 0) return;
  updateStatus('Sending the deck to the AI engine for a broadcast script…');

  const generative = document.getElementById('generativeMode').checked;
  const payload = {
    articles: deckItems.map(item => ({
      title: item.title,
      summary: item.description,
      url: item.link,
      scope: item.scope,
      source_name: item.sourceName,
      beat: item.beat || null
    })),
    anchor_name: 'Alex',
    mode: generative ? 'generative' : 'deterministic',
    lang: activeLang()
  };

  try {
    const res = await fetch(`${API_BASE}/api/generate-bulletin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`API ${res.status}`);
    const data = await res.json();

    recordHistory(deckItems);
    playScriptWithTTS(data.script, data.story_metadata || [], data.grounding_warnings || [], data.mode);
  } catch (err) {
    console.warn('AI backend unreachable; falling back to the local reader.', err);
    updateStatus('AI engine unavailable — reading the deck with the local reader instead.', true);
    readDeckAloud();
  }
}

function playScriptWithTTS(scriptText, storyMetadata, warnings, mode) {
  // Strip production cues like "[AUDIO: ...]" so display and speech stay identical (parity).
  const spoken = scriptText.replace(/\[AUDIO:.*?\]/g, ' ').replace(/\s+/g, ' ').trim();
  renderScript(spoken, storyMetadata, warnings, mode);
  speak(spoken, 'Reading the broadcast script…');
}

// Shared speech path for both readers: consistent state, lang, and stop handling.
function speak(text, statusMsg) {
  if (!('speechSynthesis' in window)) {
    updateStatus('This browser does not support the Web Speech API. The transcript remains available.', true);
    return;
  }
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  const chosen = voices[document.getElementById('voiceSelect').value];
  if (chosen) utterance.voice = chosen;
  utterance.lang = (chosen && chosen.lang) || activeLang();
  utterance.rate = parseFloat(document.getElementById('speedRate').value) || 1.0;
  utterance.pitch = 1.0;
  utterance.onend = onReadingStopped;
  utterance.onerror = onReadingStopped;

  document.getElementById('playAudioBtn').disabled = true;
  document.getElementById('generateBroadcastBtn').disabled = true;
  document.getElementById('stopAudioBtn').disabled = false;
  updateStatus(statusMsg);
  window.speechSynthesis.speak(utterance);
}

function recordHistory(items) {
  items.forEach(item => {
    if (item.link && !APP_STATE.history.includes(item.link)) APP_STATE.history.push(item.link);
  });
  if (APP_STATE.history.length > MAX_HISTORY) APP_STATE.history = APP_STATE.history.slice(-MAX_HISTORY);
  try { localStorage.setItem('sojo_history', JSON.stringify(APP_STATE.history)); } catch { /* ignore */ }
}

function clearScript() {
  const card = document.getElementById('broadcastScriptCard');
  document.getElementById('scriptTextBody').textContent = '';
  card.hidden = true;
}

// Read-by-sight: render the generated script (and its sources) so it can be read
// visually. The script body is not an aria-live region — a full-bulletin live
// announcement would be verbose and collide with the TTS playback — so instead we
// move focus to the card and announce readiness through the status region.
function renderScript(spoken, storyMetadata, warnings, mode) {
  const card = document.getElementById('broadcastScriptCard');
  const body = document.getElementById('scriptTextBody');
  body.textContent = '';

  const note = document.createElement('p');
  note.className = 'mode-note';
  note.textContent = mode === 'generative'
    ? 'AI-rephrased by a local model — verify against the sources below.'
    : 'Assembled from the sources’ own words (not AI-rephrased).';
  body.appendChild(note);

  if (warnings && warnings.length) {
    const warn = document.createElement('p');
    warn.className = 'status-indicator is-error';
    warn.textContent = `Grounding check: ${warnings.join(' ')} Verify against the sources below.`;
    body.appendChild(warn);
  }

  const para = document.createElement('p');
  para.textContent = spoken; // textContent — the script is never inserted as HTML
  body.appendChild(para);

  if (storyMetadata && storyMetadata.length) {
    const sources = document.createElement('div');
    sources.className = 'script-sources';

    const srcHeading = document.createElement('h3');
    srcHeading.textContent = 'Sources in this bulletin';
    sources.appendChild(srcHeading);

    const list = document.createElement('ul');
    storyMetadata.forEach(story => {
      const li = document.createElement('li');
      li.appendChild(sourceLink(story.url, `${story.source} — ${story.title}`));
      const edge = edgeReaderLink(story.url);
      if (edge) li.appendChild(edge);
      list.appendChild(li);
    });
    sources.appendChild(list);
    body.appendChild(sources);
  }

  card.hidden = false;
  card.focus();
}

// A source link straight from the feed/metadata (link lineage), opening the publisher.
function sourceLink(url, label) {
  const a = document.createElement('a');
  a.className = 'source-link';
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = `${label} (opens in a new tab)`;
  return a;
}

// Edge-only: a `read:` link that opens the article in Edge's Immersive Reader.
// Returns null in other browsers so no dead link is shown.
function edgeReaderLink(url) {
  if (!IS_EDGE || !url) return null;
  const a = document.createElement('a');
  a.className = 'source-link edge-reader-link';
  a.href = `read:${url}`;
  a.textContent = 'Open in Edge Immersive Reader';
  return a;
}

function stopReading() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  onReadingStopped();
}

function onReadingStopped() {
  const idle = APP_STATE.currentDeck.length === 0;
  document.getElementById('stopAudioBtn').disabled = true;
  document.getElementById('playAudioBtn').disabled = idle;
  document.getElementById('generateBroadcastBtn').disabled = idle;
}

function updateStatus(msg, isError = false) {
  const statusEl = document.getElementById('statusMessage');
  statusEl.textContent = msg;
  statusEl.classList.toggle('is-error', isError);
}
