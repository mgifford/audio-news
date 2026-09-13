/**
 * Phase 1 Local State Controller
 * Manages local preferences, client-side RSS fetching, and Web Speech API playback.
 *
 * Design boundaries (see PHASE0.md):
 * - Extractive only: the deck shows and reads the feed's own title and summary. No text is invented.
 * - Link lineage: source links come straight from the feed and are attached by this UI, never rewritten.
 * - Transcript parity: the text read aloud is exactly the text shown on the card.
 * - No unsafe rendering: feed content is inserted with textContent, never innerHTML.
 */

// The visible summary and the spoken summary use the same capped string, so audio matches transcript.
const MAX_DESC = 320;

// History cap: number of recently-read story links remembered locally.
const MAX_HISTORY = 50;

const APP_STATE = {
  theme: localStorage.getItem('sojo_theme') || 'dark',
  ratios: readJSON('sojo_ratios', { local: 1, regional: 1, national: 2, international: 1 }),
  history: readJSON('sojo_history', []),
  currentDeck: [],
  sources: null
};

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

  // Save settings locally.
  document.getElementById('saveSettingsBtn').addEventListener('click', () => {
    try { localStorage.setItem('sojo_ratios', JSON.stringify(APP_STATE.ratios)); } catch { /* ignore */ }
    closeDrawer();
    updateStatus('Deck ratios saved locally.');
  });

  // Fetch a fresh deck.
  document.getElementById('fetchDeckBtn').addEventListener('click', generateNewsDeck);

  // Audio controls.
  document.getElementById('playAudioBtn').addEventListener('click', readDeckAloud);
  document.getElementById('generateBroadcastBtn').addEventListener('click', () => generateAIBroadcastScript(APP_STATE.currentDeck));
  document.getElementById('stopAudioBtn').addEventListener('click', stopReading);

  // If the tab is closed or navigated, stop any speech.
  window.addEventListener('beforeunload', () => window.speechSynthesis && window.speechSynthesis.cancel());
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

  for (const geo of geoCategories) {
    const targetCount = APP_STATE.ratios[geo];
    if (targetCount <= 0) continue;

    const availableSources = APP_STATE.sources.geography[geo];
    if (!availableSources || availableSources.length === 0) continue;

    // Pick a random feed within this scope.
    const source = availableSources[Math.floor(Math.random() * availableSources.length)];
    const items = await fetchRSSFeed(source);

    // Skip stories already in local history.
    const freshItems = items.filter(item => !APP_STATE.history.includes(item.link));

    freshItems.slice(0, targetCount).forEach(item => {
      deck.push({
        scope: geo,
        sourceName: source.name,
        title: item.title,
        description: item.description,
        link: item.link
      });
    });
  }

  APP_STATE.currentDeck = deck;
  renderQueue(deck);
  queue.setAttribute('aria-busy', 'false');

  const canPlay = deck.length > 0 && 'speechSynthesis' in window;
  document.getElementById('playAudioBtn').disabled = !canPlay;
  document.getElementById('generateBroadcastBtn').disabled = !canPlay;

  if (deck.length > 0) {
    updateStatus(`Built a fresh deck with ${deck.length} ${deck.length === 1 ? 'story' : 'stories'}.`);
  } else {
    updateStatus('No new stories found. Try adjusting the sliders, or the feeds may be unreachable from this browser.');
  }
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
    tag.textContent = `${story.scope.toUpperCase()} — ${story.sourceName}`;

    const heading = document.createElement('h3');
    heading.textContent = story.title;

    const summary = document.createElement('p');
    summary.textContent = story.description;

    const link = document.createElement('a');
    link.className = 'source-link';
    link.href = story.link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = `Verify source at ${story.sourceName} (opens in a new tab)`;

    card.append(tag, heading, summary, link);
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
  clearTranscript(); // the cards themselves are the transcript in local mode
  speak(broadcastScript, 'Reading the deck aloud…');
}

// Phase 2 broadcast: send the deck to the backend, then read the returned script.
async function generateAIBroadcastScript(deckItems) {
  if (!deckItems || deckItems.length === 0) return;
  updateStatus('Sending the deck to the AI engine for a broadcast script…');

  const payload = {
    articles: deckItems.map(item => ({
      title: item.title,
      summary: item.description,
      url: item.link,
      scope: item.scope,
      source_name: item.sourceName
    })),
    anchor_name: 'Alex'
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
    playScriptWithTTS(data.script, data.story_metadata || [], data.grounding_warnings || []);
  } catch (err) {
    console.warn('AI backend unreachable; falling back to the local reader.', err);
    updateStatus('AI engine unavailable — reading the deck with the local reader instead.', true);
    readDeckAloud();
  }
}

function playScriptWithTTS(scriptText, storyMetadata, warnings) {
  // Strip production cues like "[AUDIO: ...]" so display and speech stay identical (parity).
  const spoken = scriptText.replace(/\[AUDIO:.*?\]/g, ' ').replace(/\s+/g, ' ').trim();
  renderTranscript(spoken, storyMetadata, warnings);
  speak(spoken, 'Reading the AI broadcast script…');
}

// Shared speech path for both readers: consistent state, lang, and stop handling.
function speak(text, statusMsg) {
  if (!('speechSynthesis' in window)) {
    updateStatus('This browser does not support the Web Speech API. The transcript remains available.', true);
    return;
  }
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'en';
  utterance.rate = 1.0;
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

function clearTranscript() {
  const region = document.getElementById('transcript');
  region.textContent = '';
  region.hidden = true;
}

// Show the AI script as a visible, verifiable transcript with source links (link lineage).
function renderTranscript(spoken, storyMetadata, warnings) {
  const region = document.getElementById('transcript');
  region.textContent = '';

  const heading = document.createElement('h2');
  heading.textContent = 'Broadcast transcript';
  region.appendChild(heading);

  if (warnings && warnings.length) {
    const warn = document.createElement('p');
    warn.className = 'status-indicator is-error';
    warn.textContent = `Grounding check: ${warnings.join(' ')} Verify against the sources below.`;
    region.appendChild(warn);
  }

  const body = document.createElement('p');
  body.textContent = spoken; // textContent — the script is never inserted as HTML
  region.appendChild(body);

  if (storyMetadata && storyMetadata.length) {
    const srcHeading = document.createElement('h3');
    srcHeading.textContent = 'Sources in this bulletin';
    region.appendChild(srcHeading);

    const list = document.createElement('ul');
    storyMetadata.forEach(story => {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.className = 'source-link';
      a.href = story.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = `${story.source} — ${story.title} (opens in a new tab)`;
      li.appendChild(a);
      list.appendChild(li);
    });
    region.appendChild(list);
  }

  region.hidden = false;
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
