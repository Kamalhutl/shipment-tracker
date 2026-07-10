/**
 * FreightPulse — app.js
 * Vanilla JS front-end for the Shipment Tracking platform.
 *
 * Sections:
 *   1.  Constants & DOM refs
 *   2.  Theme (light / dark)
 *   3.  Navigation (scroll shadow + mobile hamburger)
 *   4.  Loading state (progress bar + step text)
 *   5.  Error panel
 *   6.  Helpers (escHtml, pick, fmtDate, hideAll, setSearchError, validateBL)
 *   7.  Results rendering (summary, route, timeline, containers, raw JSON)
 *   8.  Tracking API call (fetch wrapper, typed UserError)
 *   9.  Event listeners
 *  10.  Init
 */

'use strict';

/* ─── 1. CONSTANTS & DOM REFS ─────────────────────────────────────────────── */

const API_ENDPOINT   = '/api/track';
const API_TIMEOUT_MS = 45_000;  // FIX: was 180_000 (3 min) → now 45s
const BL_PATTERN = /^[A-Z0-9\-]{5,40}$/;

const LOADING_STEPS = [
  'Connecting to carrier…',
  'Resolving BL number…',
  'Fetching vessel data…',
  'Loading port events…',
  'Compiling results…',
];

const $ = (id) => document.getElementById(id);

// Search
let blInput;
let trackBtn;
let searchError;
let searchBox;

// Loading
let loadingSection;
let progressFill;
let loadingStepText;

// Error
let errorSection;
let errorMessage;
let retryBtn;

// Results header
let resultsSection;
let resultsBLNumber;
let statusBadge;
let statusBadgeText;
let newTrackBtn;

// Summary cards
let cardCarrier;
let cardBooking;
let cardVessel;
let cardVoyage;
let cardPOL;
let cardPOD;
let cardETD;
let cardETA;

// Sub-sections
let routeMap;
let timeline;
let timelineCount;
let containersGrid;
let containersCount;
let rawData;

// Nav & footer
let themeToggle;
let navHamburger;
let mobileMenu;
let footerYear;

function bindDOMRefs() {
  blInput      = $('blInput');
  trackBtn     = $('trackBtn');
  searchError  = $('searchError');
  searchBox    = $('searchBox');

  loadingSection  = $('loadingSection');
  progressFill    = $('progressFill');
  loadingStepText = $('loadingStepText');

  errorSection = $('errorSection');
  errorMessage = $('errorMessage');
  retryBtn     = $('retryBtn');

  resultsSection  = $('resultsSection');
  resultsBLNumber = $('resultsBLNumber');
  statusBadge     = $('statusBadge');
  statusBadgeText = $('statusBadgeText');
  newTrackBtn     = $('newTrackBtn');

  cardCarrier = $('cardCarrier');
  cardBooking = $('cardBooking');
  cardVessel  = $('cardVessel');
  cardVoyage  = $('cardVoyage');
  cardPOL     = $('cardPOL');
  cardPOD     = $('cardPOD');
  cardETD     = $('cardETD');
  cardETA     = $('cardETA');

  routeMap        = $('routeMap');
  timeline        = $('timeline');
  timelineCount   = $('timelineCount');
  containersGrid  = $('containersGrid');
  containersCount = $('containersCount');
  rawData         = $('rawData');

  themeToggle  = $('themeToggle');
  navHamburger = $('navHamburger');
  mobileMenu   = $('mobileMenu');
  footerYear   = $('footerYear');
}


/* ─── 2. THEME ────────────────────────────────────────────────────────────── */

const THEME_KEY = 'fp-theme';

function getPreferredTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === 'dark' || stored === 'light') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(THEME_KEY, theme);
  themeToggle.setAttribute(
    'aria-label',
    theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode',
  );
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(current === 'dark' ? 'light' : 'dark');
}


/* ─── 3. NAVIGATION ───────────────────────────────────────────────────────── */

function initNav() {
  const navWrapper = document.querySelector('.nav-wrapper');

  const onScroll = () => navWrapper.classList.toggle('scrolled', window.scrollY > 10);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  navHamburger.addEventListener('click', () => {
    const isOpen = navHamburger.getAttribute('aria-expanded') === 'true';
    navHamburger.setAttribute('aria-expanded', String(!isOpen));
    mobileMenu.classList.toggle('open', !isOpen);
    mobileMenu.setAttribute('aria-hidden', String(isOpen));
  });
}

function closeMobileMenu() {
  navHamburger.setAttribute('aria-expanded', 'false');
  mobileMenu.classList.remove('open');
  mobileMenu.setAttribute('aria-hidden', 'true');
}

window.closeMobileMenu = closeMobileMenu;


/* ─── 4. LOADING STATE ────────────────────────────────────────────────────── */

let _loadingTimer = null;

function showLoading() {
  hideAll();
  loadingSection.hidden = false;
  progressFill.style.width = '0%';
  loadingStepText.textContent = LOADING_STEPS[0];
  loadingSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

  let progress = 0;
  _loadingTimer = setInterval(() => {
    const delta = Math.random() * 10 + (progress < 40 ? 8 : 3);
    progress = Math.min(progress + delta, 88);
    progressFill.style.width = `${progress}%`;

    const stepIdx = Math.min(
      Math.floor((progress / 90) * LOADING_STEPS.length),
      LOADING_STEPS.length - 1,
    );
    loadingStepText.textContent = LOADING_STEPS[stepIdx];
  }, 600);
}

function completeLoading() {
  clearInterval(_loadingTimer);
  progressFill.style.width = '100%';
  loadingStepText.textContent = 'Done!';
}

function hideLoading() {
  clearInterval(_loadingTimer);
  _loadingTimer = null;
  loadingSection.hidden = true;
  progressFill.style.width = '0%';
}


/* ─── 5. ERROR PANEL ──────────────────────────────────────────────────────── */

function showError(message) {
  hideLoading();
  resultsSection.hidden = true;
  errorMessage.textContent =
    message || 'We could not retrieve shipment data. Please check your BL number and try again.';
  errorSection.hidden = false;
  errorSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}


/* ─── 6. HELPERS ──────────────────────────────────────────────────────────── */

function escHtml(str) {
  if (str === undefined || str === null) return 'N/A';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function pick(obj, ...keys) {
  if (!obj || typeof obj !== 'object') return 'N/A';
  for (const key of keys) {
    const val = obj[key];
    if (val !== undefined && val !== null && String(val).trim() !== '') {
      return String(val).trim();
    }
  }
  return 'N/A';
}

function fmtDate(raw) {
  if (!raw || raw === 'N/A') return 'N/A';
  try {
    const iso = raw.replace(' ', 'T');
    const d = new Date(iso);
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch {
    return raw;
  }
}

function fmtDateTime(raw) {
  if (!raw || raw === 'N/A') return 'N/A';
  try {
    const iso = raw.replace(' ', 'T');
    const d = new Date(iso);
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return raw;
  }
}

function resolveStatusClass(status) {
  if (!status || status === 'N/A') return '';
  const s = status.toLowerCase();
  if (s.includes('delay') || s.includes('hold') || s.includes('wait') || s.includes('pending')) {
    return 'status-badge--warn';
  }
  if (s.includes('error') || s.includes('fail') || s.includes('cancel') || s.includes('except')) {
    return 'status-badge--err';
  }
  return '';
}

function hideAll() {
  loadingSection.hidden = true;
  errorSection.hidden   = true;
  resultsSection.hidden = true;
}

function setSearchError(msg) {
  if (msg) {
    searchError.textContent = msg;
    searchError.hidden = false;
    searchBox.setAttribute('aria-invalid', 'true');
  } else {
    searchError.hidden = true;
    searchBox.removeAttribute('aria-invalid');
  }
}

function validateBL(raw) {
  const cleaned = raw.trim().toUpperCase();
  if (!cleaned) {
    setSearchError('Please enter a Bill of Lading number.');
    return null;
  }
  if (cleaned.length < 5) {
    setSearchError('BL Number must be at least 5 characters.');
    return null;
  }
  if (cleaned.length > 40) {
    setSearchError('BL Number must not exceed 40 characters.');
    return null;
  }
  if (!BL_PATTERN.test(cleaned)) {
    setSearchError('BL Number may only contain letters, digits, and hyphens.');
    return null;
  }
  setSearchError('');
  return cleaned;
}


/* ─── 7. RESULTS RENDERING ────────────────────────────────────────────────── */

function renderSummary(bl, data) {
  resultsBLNumber.textContent = escHtml(
    pick(data, 'bl_number', 'bl_no', 'booking_ref', 'reference') !== 'N/A'
      ? pick(data, 'bl_number', 'bl_no', 'booking_ref', 'reference')
      : bl,
  );

  const status = pick(data, 'current_status', 'status', 'shipment_status', 'container_status');
  statusBadgeText.textContent = status;
  statusBadge.className = `status-badge ${resolveStatusClass(status)}`.trim();

  cardCarrier.textContent = pick(data, 'carrier', 'carrier_name', 'shipping_line', 'line');
  cardBooking.textContent = pick(data, 'booking_no', 'booking_number', 'booking_ref', 'bl_number', 'bl_no');

  // FIX: vessel can be object {name, voyage} OR a flat string — handle both cases
  // FIX: removed duplicate cardVoyage assignment that was overriding the if/else result
  const vesselValue = data.vessel;
  if (typeof vesselValue === 'object' && vesselValue !== null) {
    cardVessel.textContent = vesselValue.name  || 'N/A';
    cardVoyage.textContent = vesselValue.voyage || pick(data, 'voyage', 'voyage_number', 'voyage_no', 'voy');
  } else {
    cardVessel.textContent = pick(data, 'vessel', 'vessel_name', 'ship', 'ship_name');
    cardVoyage.textContent = pick(data, 'voyage', 'voyage_number', 'voyage_no', 'voy');
  }

  cardPOL.textContent = pick(data, 'pol', 'port_of_loading', 'origin_port', 'load_port', 'from_port');
  cardPOD.textContent = pick(data, 'pod', 'port_of_discharge', 'destination_port', 'discharge_port', 'to_port');
  cardETD.textContent = fmtDate(pick(data, 'etd', 'departure_date', 'etd_date', 'sailing_date'));
  cardETA.textContent = fmtDate(pick(data, 'eta', 'arrival_date', 'eta_date', 'expected_arrival'));
}

// FIX: renderRoute was split across two scopes — makeStop/makeConnector were orphaned
// outside the function body causing a SyntaxError that broke the entire JS file.
// Merged everything into one clean function.
function renderRoute(data) {
  const origin      = data.pol         || data.origin      || 'Origin';
  const destination = data.pod         || data.destination || 'Destination';
  const etd         = fmtDate(pick(data, 'etd', 'departure_date', 'etd_date', 'sailing_date'));
  const eta         = fmtDate(pick(data, 'eta', 'arrival_date',  'eta_date', 'expected_arrival'));

  // Check for transshipment port
  const transship   = data.transshipment || data.via || data.transship_port || null;
  const hasTransship = transship && String(transship).trim() !== '';

  routeMap.innerHTML = '';

  function makeStop(port, label, dateStr, modifier) {
    const el = document.createElement('div');
    el.className = 'route-stop';
    const dotClass = modifier ? `route-stop__dot ${modifier}` : 'route-stop__dot';
    el.innerHTML = `
      <span class="${dotClass}" aria-hidden="true"></span>
      <span class="route-stop__port">${escHtml(port)}</span>
      <span class="route-stop__label">${escHtml(label)}</span>
      ${dateStr !== 'N/A' ? `<span class="route-stop__date">${escHtml(dateStr)}</span>` : ''}
    `;
    return el;
  }

  function makeConnector() {
    const el = document.createElement('div');
    el.className = 'route-connector';
    el.innerHTML = `
      <div class="route-connector__line"></div>
      <span class="route-connector__arrow" aria-hidden="true">▶</span>
    `;
    return el;
  }

  routeMap.appendChild(makeStop(origin, 'Origin', etd, ''));
  routeMap.appendChild(makeConnector());

  if (hasTransship) {
    routeMap.appendChild(makeStop(transship, 'Transshipment', 'N/A', 'route-stop__dot--transship'));
    routeMap.appendChild(makeConnector());
  }

  routeMap.appendChild(makeStop(destination, 'Destination', eta, 'route-stop__dot--destination'));
}

function renderTimeline(data) {
  const events =
    data.events      ||
    data.timeline    ||
    data.port_events ||
    data.history     ||
    data.activities  ||
    [];

  timeline.innerHTML = '';

  if (!Array.isArray(events) || events.length === 0) {
    timeline.innerHTML = '<li class="timeline__empty">No events recorded yet.</li>';
    timelineCount.textContent = '';
    return;
  }

  timelineCount.textContent = String(events.length);

  events.forEach((ev, idx) => {
    const status   = pick(ev, 'status', 'event', 'description', 'activity', 'event_type');
    const location = pick(ev, 'location', 'port', 'place', 'terminal', 'facility');
    const date     = pick(ev, 'date', 'event_date', 'datetime', 'timestamp', 'actual_date');
    const time     = pick(ev, 'time', 'event_time', 'actual_time');

    const dateTimeStr = (() => {
      if (date === 'N/A') return 'N/A';
      if (time !== 'N/A') return fmtDateTime(`${date} ${time}`);
      return fmtDate(date);
    })();

    const dotModifier = idx === 0 ? '' : 'timeline-item__dot--completed';

    const li = document.createElement('li');
    li.className = 'timeline-item';
    li.innerHTML = `
      <span class="timeline-item__dot ${dotModifier}" aria-hidden="true"></span>
      <div class="timeline-item__body">
        <p class="timeline-item__status">${escHtml(status)}</p>
        ${location !== 'N/A' ? `
          <p class="timeline-item__location">
            <span class="timeline-item__location-dot" aria-hidden="true"></span>
            ${escHtml(location)}
          </p>
        ` : ''}
        <p class="timeline-item__datetime">${escHtml(dateTimeStr)}</p>
      </div>
    `;
    timeline.appendChild(li);
  });
}

function renderContainers(data) {
  const containers =
    data.containers     ||
    data.container_list ||
    data.cntr           ||
    data.boxes          ||
    [];

  containersGrid.innerHTML = '';

  if (!Array.isArray(containers) || containers.length === 0) {
    containersGrid.innerHTML = '<p class="containers-empty">No container data available.</p>';
    containersCount.textContent = '';
    return;
  }

  containersCount.textContent = String(containers.length);

  containers.forEach((c) => {
    const number = pick(c, 'number', 'container_number', 'cntr_no', 'container_id', 'id');
    const type   = pick(c, 'type', 'container_type', 'size_type', 'cntr_type', 'equipment_type');
    const status = pick(c, 'status', 'current_status', 'container_status', 'cntr_status');
    const weight = pick(c, 'weight', 'gross_weight', 'vgm', 'cargo_weight', 'tare_weight');

    const card = document.createElement('article');
    card.className = 'container-card';
    card.setAttribute('aria-label', `Container ${escHtml(number)}`);
    card.innerHTML = `
      <div class="container-card__header">
        <span class="container-card__number">${escHtml(number)}</span>
        ${type !== 'N/A' ? `<span class="container-card__type">${escHtml(type)}</span>` : ''}
      </div>
      <div class="container-card__body">
        <div class="container-card__field">
          <span class="container-card__label">Weight</span>
          <span class="container-card__value">${escHtml(weight)}</span>
        </div>
        <div class="container-card__field">
          <span class="container-card__label">Type</span>
          <span class="container-card__value">${escHtml(type)}</span>
        </div>
      </div>
      <div class="container-card__status">
        <span class="container-card__status-dot" aria-hidden="true"></span>
        ${escHtml(status)}
      </div>
    `;
    containersGrid.appendChild(card);
  });
}

function renderRawData(data) {
  try {
    rawData.textContent = JSON.stringify(data, null, 2);
  } catch {
    rawData.textContent = '[Unable to serialise response]';
  }
}

function showResults(bl, data) {
  renderSummary(bl, data);
  renderRoute(data);
  renderTimeline(data);
  renderContainers(data);
  renderRawData(data);

  hideAll();
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}


/* ─── 8. TRACKING API CALL ────────────────────────────────────────────────── */

class UserError extends Error {
  constructor(msg) {
    super(msg);
    this.name = 'UserError';
  }
}

async function trackShipment(bl) {
  const controller = new AbortController();
  const timeoutId  = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

  let response;
  try {
    response = await fetch(API_ENDPOINT, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ bl_number: bl }),
      signal:  controller.signal,
    });
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new UserError(
        `Request timed out after ${API_TIMEOUT_MS / 1000} s. Please try again.`,
      );
    }
    throw new UserError(
      'Cannot reach the tracking service. Please check your connection and try again.',
    );
  }

  clearTimeout(timeoutId);

  switch (response.status) {
    case 400:
      throw new UserError('Bad request — please verify your BL Number format.');
    case 422:
      throw new UserError('Invalid BL Number format. Please check your input.');
    case 404:
      throw new UserError(
        'Shipment not found. The BL Number may be incorrect or not yet in the carrier system.',
      );
    case 500:
    case 502:
    case 503:
      throw new UserError(
        'The tracking service is temporarily unavailable. Please try again shortly.',
      );
    default:
      if (!response.ok) {
        throw new UserError(
          `Tracking request failed (HTTP ${response.status}). Please try again.`,
        );
      }
  }

  let body;
  try {
    body = await response.json();
  } catch {
    throw new UserError(
      'Received an unreadable response from the server. Please try again.',
    );
  }

  if (!body || typeof body !== 'object') {
    throw new UserError('Empty or unrecognised response from the tracking service.');
  }

  if (body.success === false) {
    throw new UserError(
      body.message || 'The carrier returned an error for this BL Number.',
    );
  }

  const shipmentData = body.data ?? body;
  if (!shipmentData || typeof shipmentData !== 'object') {
    throw new UserError('No shipment data found in the server response.');
  }

  return shipmentData;
}


/* ─── 9. EVENT LISTENERS ──────────────────────────────────────────────────── */

async function handleTrack() {
  const bl = validateBL(blInput.value);
  if (!bl) return;

  trackBtn.disabled = true;
  blInput.disabled  = true;

  showLoading();

  try {
    const data = await trackShipment(bl);
    completeLoading();
    await new Promise((resolve) => setTimeout(resolve, 350));
    showResults(bl, data);
  } catch (err) {
    hideLoading();
    showError(err instanceof UserError ? err.message : 'An unexpected error occurred. Please try again.');
  } finally {
    trackBtn.disabled = false;
    blInput.disabled  = false;
  }
}

function handleNewTrack() {
  hideAll();
  blInput.value = '';
  setSearchError('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  setTimeout(() => blInput.focus(), 400);
}

function initEventListeners() {
  trackBtn.addEventListener('click', handleTrack);

  blInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleTrack();
  });

  blInput.addEventListener('input', () => {
    if (!searchError.hidden) setSearchError('');
  });

  blInput.addEventListener('input', () => {
    const pos = blInput.selectionStart;
    const upper = blInput.value.toUpperCase();
    if (blInput.value !== upper) {
      blInput.value = upper;
      blInput.setSelectionRange(pos, pos);
    }
  });

  retryBtn.addEventListener('click', () => {
    hideAll();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    setTimeout(() => blInput.focus(), 400);
  });

  newTrackBtn.addEventListener('click', handleNewTrack);

  themeToggle.addEventListener('click', toggleTheme);

  document.addEventListener('click', (e) => {
    if (
      mobileMenu.classList.contains('open') &&
      !mobileMenu.contains(e.target) &&
      !navHamburger.contains(e.target)
    ) {
      closeMobileMenu();
    }
  });
}


/* ─── 10. INIT ────────────────────────────────────────────────────────────── */

function init() {
  bindDOMRefs();
  footerYear.textContent = new Date().getFullYear();
  applyTheme(getPreferredTheme());
  initNav();
  initEventListeners();
  if (!window.matchMedia('(hover: none)').matches) {
    blInput.focus();
  }
}

document.addEventListener('DOMContentLoaded', init);
