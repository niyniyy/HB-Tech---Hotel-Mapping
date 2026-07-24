/* HB Hotel Mapping — review console
   No build step: plain ES modules-free JS so it runs straight from the container. */

const API = '/api/v1';

const PAGES = [
  { id: 'dashboard',  label: 'Dashboard',        icon: '▤', section: 'Overview' },
  { id: 'discarded',  label: 'Discarded Records', icon: '⚑', section: 'Overview', badge: 'discarded_unread' },
  { id: 'masters',    label: 'Master Hotels',    icon: '⌂', section: 'Mapping' },
  { id: 'review',     label: 'Manual Review',    icon: '◷', section: 'Mapping', badge: 'pending_review' },
  { id: 'provisional', label: 'Pending Masters', icon: '◐', section: 'Mapping', badge: 'provisional_masters' },
  { id: 'duplicates', label: 'Duplicate Masters', icon: '⧉', section: 'Quality' },
  { id: 'splits',     label: 'Known Splits',     icon: '⊟', section: 'Quality' },
  { id: 'integrity',  label: 'Integrity Checks', icon: '✓', section: 'Quality' },
  { id: 'suppliers',  label: 'Supplier Quality', icon: '⇄', section: 'Quality' },
  { id: 'import',     label: 'Import Data',      icon: '↧', section: 'Data' },
  { id: 'pipeline',   label: 'Run Pipeline',     icon: '▶', section: 'Data' },
  { id: 'exports',    label: 'Exports',          icon: '↥', section: 'Data' },
];

const App = {
  page: 'dashboard',
  data: {},
  state: { selected: new Set(), master: null, discardFilter: null, unreadOnly: false },

  async init() {
    window.addEventListener('hashchange', () => this.route());
    this.route();
  },

  route() {
    const [page, arg] = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/');
    this.page = PAGES.some(p => p.id === page) ? page : 'dashboard';
    this.arg = arg ? decodeURIComponent(arg) : null;
    this.refresh();
  },

  async refresh() {
    try {
      this.data.dashboard = await get(`${API}/dashboard`);
    } catch (e) { /* dashboard is best-effort for badges */ }
    this.renderNav();
    const title = PAGES.find(p => p.id === this.page);
    document.getElementById('pageTitle').textContent = title ? title.label : 'Dashboard';
    const foot = this.data.dashboard?.stats;
    document.getElementById('footStatus').textContent =
      foot ? `${fmt(foot.active_masters)} masters · ${fmt(foot.supplier_hotels)} records` : '—';
    await this[`render_${this.page}`]().catch(err => {
      el('view').innerHTML = `<div class="alert alert-danger"><div class="alert-icon">!</div>
        <div class="alert-body"><div class="alert-title">Could not load this page</div>
        <div class="alert-text">${esc(err.message)}</div></div></div>`;
    });
  },

  renderNav() {
    const stats = this.data.dashboard?.stats || {};
    let html = '', section = null;
    for (const p of PAGES) {
      if (p.section !== section) { section = p.section; html += `<div class="nav-section">${section}</div>`; }
      const count = p.badge ? (stats[p.badge] || 0) : 0;
      html += `<div class="nav-item ${this.page === p.id ? 'active' : ''}" onclick="location.hash='#/${p.id}'">
        <span class="nav-icon">${p.icon}</span><span>${p.label}</span>
        ${count > 0 ? `<span class="nav-badge">${count > 999 ? '999+' : count}</span>` : ''}
      </div>`;
    }
    el('nav').innerHTML = html;
  },

  /* ── Dashboard ──────────────────────────────────────────────────────── */

  async render_dashboard() {
    const d = this.data.dashboard || await get(`${API}/dashboard`);
    const s = d.stats;
    // Fetched separately and tolerated failing: accuracy needs a reference
    // mapping, and a database without one should still show a dashboard.
    const acc = await get(`${API}/evaluation/accuracy`).catch(() => null);
    const gate = await get(`${API}/evaluation/gate`).catch(() => null);
    const dupc = await get(`${API}/duplicates/count`).catch(() => null);
    const tierTotal = d.confidence.reduce((a, t) => a + Number(t.n), 0) || 1;
    const tierColour = { TIER1: 'var(--ok)', TIER2: 'var(--info)', TIER3: 'var(--warn)', TIER4: 'var(--danger)' };

    let banner = '';
    if (s.discarded_unread > 0) {
      banner = `<div class="alert alert-warn">
        <div class="alert-icon">⚑</div>
        <div class="alert-body">
          <div class="alert-title">${fmt(s.discarded_unread)} record${s.discarded_unread === 1 ? '' : 's'} were left out of mapping and need review</div>
          <div class="alert-text">These hotels could not be mapped safely — usually a supplier data problem.
            Nothing was deleted; each one is listed with the reason.</div>
        </div>
        <button class="btn-primary btn-sm" onclick="location.hash='#/discarded'">Review now</button>
      </div>`;
    } else if (s.discarded > 0) {
      banner = `<div class="alert alert-ok"><div class="alert-icon">✓</div><div class="alert-body">
        <div class="alert-title">All discarded records reviewed</div>
        <div class="alert-text">${fmt(s.discarded)} record(s) excluded from mapping, all acknowledged.</div>
      </div></div>`;
    }

    if (s.queue_failed > 0) {
      banner += `<div class="alert alert-danger"><div class="alert-icon">!</div><div class="alert-body">
        <div class="alert-title">${fmt(s.queue_failed)} record(s) failed during processing</div>
        <div class="alert-text">These errored rather than being deliberately excluded. Check worker logs.</div>
      </div></div>`;
    }

    el('view').innerHTML = `
      ${gateBanner(gate)}
      ${banner}
      <div class="stat-grid">
        ${stat('Master hotels', fmt(s.active_masters), 'unique properties', 'ok')}
        ${stat('Supplier records', fmt(s.supplier_hotels), `${fmt(s.auto_mapped)} auto-matched`)}
        ${stat('Discarded', fmt(s.discarded), `${fmt(s.discarded_unread)} unread`, s.discarded_unread ? 'warn' : '')}
        ${stat('Pending review', fmt(s.pending_review), 'awaiting a decision', s.pending_review ? 'warn' : '')}
        ${stat('Reviewer decisions', fmt(s.reviewer_assertions), 'permanent, never overridden')}
        ${stat('Retired IDs', fmt(s.merged_ids), 'still resolve to successor')}
        ${stat('Duplicate masters', dupc ? fmt(dupc.duplicate_pairs) : '…',
               dupc?.duplicate_pairs ? 'pairs to merge — see Duplicate Masters' : 'none detected',
               dupc?.duplicate_pairs ? 'warn' : 'ok')}
      </div>

      ${accuracyCard(acc)}

      <div class="row">
        <div class="col card">
          <div class="card-head"><h2>Match confidence</h2></div>
          <div class="card-body">
            <div class="bar" style="margin-bottom:12px">
              ${d.confidence.map(t => `<span style="width:${(t.n / tierTotal * 100).toFixed(1)}%;background:${tierColour[t.tier] || '#ccc'}"></span>`).join('')}
            </div>
            <table><tbody>
            ${d.confidence.map(t => `<tr>
              <td><span class="chip chip-${(t.tier || '').toLowerCase()}">${t.tier}</span></td>
              <td class="muted small">${tierDesc(t.tier)}</td>
              <td class="num">${fmt(t.n)}</td>
              <td class="num muted">${(t.n / tierTotal * 100).toFixed(1)}%</td>
            </tr>`).join('')}
            </tbody></table>
            <p class="small muted" style="margin:12px 0 0">
              TIER1 and TIER2 are safe to publish without review. TIER3/TIER4 hold whatever residual risk exists.
            </p>
          </div>
        </div>

      </div>

      <div class="card">
        <div class="card-head">
          <h2>By supplier</h2>
          <div class="spacer"></div>
          <span class="small muted">Mapped % is of that supplier's own records, so the columns are comparable across feeds of different sizes.</span>
        </div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr>
            <th>Supplier</th>
            <th class="num">Supplied</th>
            <th class="num">Mapped</th>
            <th>Mapped %</th>
            <th class="num">Auto mapped</th>
            <th class="num">New master</th>
            <th class="num">Manual mapped</th>
            <th class="num">Manual new master</th>
            <th class="num">Discarded</th>
          </tr></thead>
          <tbody>${d.suppliers.map(x => {
            const pct = Number(x.mapped_pct ?? 0);
            return `<tr>
              <td><strong>${esc(x.supplier_name)}</strong></td>
              <td class="num">${fmt(x.supplied)}</td>
              <td class="num">${fmt(x.mapped)}</td>
              <td style="min-width:120px">
                <div class="bar" style="margin-bottom:3px">
                  <span style="width:${pct.toFixed(1)}%;background:${pct >= 95 ? 'var(--ok)' : pct >= 85 ? 'var(--info)' : 'var(--warn)'}"></span>
                </div>
                <span class="small muted">${pct.toFixed(1)}%</span>
              </td>
              <td class="num">${fmt(x.auto_mapped)}</td>
              <td class="num">${fmt(x.new_master)}</td>
              <td class="num">${x.manual_mapped > 0 ? fmt(x.manual_mapped) : '<span class="muted">—</span>'}</td>
              <td class="num">${x.manual_new_master > 0 ? fmt(x.manual_new_master) : '<span class="muted">—</span>'}</td>
              <td class="num">${x.discarded > 0 ? `<span class="chip chip-medium">${fmt(x.discarded)}</span>` : '<span class="muted">—</span>'}</td>
            </tr>`;
          }).join('')}
          ${supplierTotals(d.suppliers)}
          </tbody>
        </table></div></div>
      </div>`;
  },

  /* ── Discarded records ──────────────────────────────────────────────── */

  async render_discarded() {
    const [summary, list] = await Promise.all([
      get(`${API}/discarded/summary`),
      get(`${API}/discarded?limit=200${this.state.discardFilter ? `&reason=${this.state.discardFilter}` : ''}${this.state.unreadOnly ? '&unread_only=true' : ''}`)
    ]);

    const banner = summary.unread > 0
      ? `<div class="alert alert-warn"><div class="alert-icon">⚑</div><div class="alert-body">
           <div class="alert-title">${fmt(summary.unread)} record(s) left out of mapping, not yet reviewed</div>
           <div class="alert-text">Nothing has been deleted. Each record is kept with the reason it could not be mapped,
             and can be re-processed once the underlying data is corrected.</div>
         </div>
         <button class="btn-sm" onclick="App.ackAll()">Mark all reviewed</button></div>`
      : `<div class="alert alert-ok"><div class="alert-icon">✓</div><div class="alert-body">
           <div class="alert-title">Nothing new</div>
           <div class="alert-text">All ${fmt(summary.total)} discarded record(s) have been reviewed.</div></div></div>`;

    el('view').innerHTML = `
      ${banner}
      <div class="card"><div class="card-head"><h2>Why records were left out</h2>
        <div class="spacer"></div>${exportBtn('discarded-records')}</div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Reason</th><th>What it means</th><th>Suggested action</th><th class="num">Records</th><th class="num">Unread</th><th></th></tr></thead>
          <tbody>${summary.reasons.map(r => `<tr>
            <td><span class="chip chip-${r.severity}">${esc(r.label)}</span></td>
            <td class="muted small">${esc(r.detail)}</td>
            <td class="small">${esc(r.action)}</td>
            <td class="num">${fmt(r.total)}</td>
            <td class="num">${r.unread > 0 ? `<strong>${fmt(r.unread)}</strong>` : '—'}</td>
            <td><button class="btn-sm" onclick="App.filterDiscard('${r.flag_reason}')">View</button></td>
          </tr>`).join('')}</tbody>
        </table></div></div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2>Records${this.state.discardFilter ? ` — ${esc(this.state.discardFilter)}` : ''}</h2>
          <div class="spacer"></div>
          ${this.state.discardFilter ? `<button class="btn-sm" onclick="App.filterDiscard(null)">Clear filter</button>` : ''}
          <button class="btn-sm" onclick="App.toggleUnread()">${this.state.unreadOnly ? 'Show all' : 'Unread only'}</button>
        </div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th></th><th>Hotel</th><th>Supplier</th><th>Supplier ID</th><th>City</th><th>Reason</th><th>Status</th></tr></thead>
          <tbody>${list.records.length ? list.records.map(r => `<tr>
            <td><input type="checkbox" class="ack" value="${r.id}"></td>
            <td><strong>${esc(r.hotel_name || '—')}</strong><div class="faint small">${esc(r.address || '')}</div></td>
            <td>${esc(r.supplier_name)}</td>
            <td class="mono">${esc(r.supplier_hotel_id)}</td>
            <td>${esc(r.city || '—')}</td>
            <td><span class="chip chip-${r.severity}">${esc(r.label)}</span></td>
            <td>${r.acknowledged_at ? '<span class="muted small">reviewed</span>' : '<span class="chip chip-new">NEW</span>'}</td>
          </tr>`).join('') : `<tr><td colspan="7" class="empty">No records</td></tr>`}</tbody>
        </table></div></div>
        ${list.records.length ? `<div class="card-head" style="border-top:1px solid var(--border);border-bottom:none">
          <button class="btn-sm" onclick="App.ackSelected()">Mark selected as reviewed</button>
          <span class="muted small">Records stay in the system and can be re-processed after the supplier corrects them.</span>
        </div>` : ''}
      </div>`;
  },

  filterDiscard(reason) { this.state.discardFilter = reason; this.refresh(); },
  toggleUnread() { this.state.unreadOnly = !this.state.unreadOnly; this.refresh(); },

  async ackSelected() {
    const ids = [...document.querySelectorAll('.ack:checked')].map(c => Number(c.value));
    if (!ids.length) return toast('Select at least one record', true);
    await post(`${API}/discarded/acknowledge`, { record_ids: ids });
    toast(`${ids.length} record(s) marked as reviewed`);
    this.refresh();
  },

  async ackAll() {
    if (!confirm('Mark every discarded record as reviewed?')) return;
    await post(`${API}/discarded/acknowledge`, { all: true, reason: this.state.discardFilter });
    toast('All records marked as reviewed');
    this.refresh();
  },

  /* ── Masters ────────────────────────────────────────────────────────── */

  async render_masters() {
    if (this.arg) return this.renderMasterDetail(this.arg);

    const f = this.state.filters || (this.state.filters = {});
    if (!this.state.facets) this.state.facets = await get(`${API}/search/facets`);
    const fx = this.state.facets;
    const emptyNote = k => fx.empty_fields.includes(k)
      ? `<span class="faint small"> — no data from current suppliers</span>` : '';

    el('view').innerHTML = `
      <div class="card"><div class="card-body">
        <input type="search" id="f_q" class="hero-search" placeholder="Enter hotel name to search"
               value="${esc(f.q || '')}">
      </div></div>

      <div class="card"><div class="card-body">
        <div class="filter-grid">
          <label for="f_master_id">Master Hotel Id:</label>
          <input type="text" id="f_master_id" placeholder="HBM-00000087" value="${esc(f.master_id || '')}">

          <label for="f_provider_hotel_id">Provider Hotel Id:</label>
          <input type="text" id="f_provider_hotel_id" placeholder="Provider Hotel Id" value="${esc(f.provider_hotel_id || '')}">

          <label for="f_provider_name">Provider Name:</label>
          <select id="f_provider_name">
            <option value="">Select Provider Name</option>
            ${fx.providers.map(p => `<option ${f.provider_name === p ? 'selected' : ''}>${esc(p)}</option>`).join('')}
          </select>

          <label for="f_chain_name">Hotel Chain Name:${emptyNote('chain_name')}</label>
          <input type="text" id="f_chain_name" placeholder="Search or Type Hotel Chain" value="${esc(f.chain_name || '')}">

          <label for="f_property_type">Property Type:${emptyNote('property_type')}</label>
          <input type="text" id="f_property_type" placeholder="Search or Type Category" value="${esc(f.property_type || '')}">

          <label for="f_country">Country:</label>
          <input type="text" id="f_country" list="dl_country" placeholder="Search Country" value="${esc(f.country || '')}">
          <datalist id="dl_country">${fx.countries.map(c => `<option value="${esc(c.country)}">`).join('')}</datalist>

          <label for="f_city">City Name:</label>
          <input type="text" id="f_city" placeholder="City Name" value="${esc(f.city || '')}">

          <label for="f_star">Star: ${starCoverageNote(fx)}</label>
          <div class="star-wrap">
            <input type="range" id="f_star" min="0" max="5" step="1" value="${f.star_min || 0}">
            <div class="star-scale"><span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span></div>
          </div>
        </div>

        <div class="filter-actions">
          <button class="btn-count" onclick="App.propertyCount()">Get Property Count</button>
          <span class="muted small" id="countOut"></span>
          <div class="spacer"></div>
          <button onclick="App.resetFilters()">Reset</button>
          <button class="btn-primary" onclick="App.runSearch(0)">Search</button>
        </div>
        <p class="small muted" style="margin:10px 0 0">Results appear as soon as any field has a value.</p>
      </div></div>

      <div id="searchResults"><div class="empty">Search for a master hotel to see results.</div></div>`;

    // Live search: any field change re-runs after a short pause.
    const ids = ['f_q','f_master_id','f_provider_hotel_id','f_provider_name','f_chain_name',
                 'f_property_type','f_country','f_city','f_star'];
    for (const id of ids) {
      const node = el(id);
      if (!node) continue;
      const evt = (node.tagName === 'SELECT' || node.type === 'range') ? 'change' : 'input';
      node.addEventListener(evt, () => this.onFilterChange());
      if (evt === 'input') node.addEventListener('keydown', e => { if (e.key === 'Enter') this.runSearch(0); });
    }

    if (this.state.lastResults) this.renderResults(this.state.lastResults);
  },

  readFilters() {
    const val = id => (el(id) ? el(id).value.trim() : '');
    const star = el('f_star') ? Number(el('f_star').value) : 0;
    const f = {
      q: val('f_q'), master_id: val('f_master_id'),
      provider_hotel_id: val('f_provider_hotel_id'), provider_name: val('f_provider_name'),
      chain_name: val('f_chain_name'), property_type: val('f_property_type'),
      country: val('f_country'), city: val('f_city'),
      star_min: star > 0 ? star : '',
    };
    this.state.filters = f;
    return f;
  },

  onFilterChange() {
    clearTimeout(this.state.searchTimer);
    this.state.searchTimer = setTimeout(() => this.runSearch(0, true), 350);
  },

  filterQuery(f, extra = {}) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...f, ...extra })) if (v !== '' && v != null) p.set(k, v);
    return p.toString();
  },

  async runSearch(offset = 0, quiet = false) {
    const f = this.readFilters();
    if (!Object.values(f).some(v => v !== '')) {
      el('searchResults').innerHTML = '<div class="empty">Search for a master hotel to see results.</div>';
      return;
    }
    if (!quiet) el('searchResults').innerHTML = '<div class="spinner">Searching…</div>';
    try {
      const d = await get(`${API}/masters/search?${this.filterQuery(f, { limit: 50, offset })}`);
      this.state.lastResults = d;
      this.renderResults(d);
    } catch (e) { toast(e.message, true); }
  },

  renderResults(d) {
    const host = el('searchResults');
    if (!host) return;
    if (!d.searched || !d.results.length) {
      // Naming the filter that emptied the result is the difference between
      // "the data is not there" and "you asked a question the data cannot
      // answer". A star filter silently excludes every unrated record, which is
      // most of them.
      const starOn = Number(el('f_star')?.value || 0) > 0;
      const hint = starOn
        ? ' The star filter excludes records with no rating, and most have none — set it back to 0 to include them.'
        : '';
      host.innerHTML = `<div class="empty">${d.searched
        ? 'Nothing matched those filters.' + hint
        : 'Search for a master hotel to see results.'}</div>`;
      return;
    }
    const from = d.offset + 1;
    host.innerHTML = `
      <div class="card"><div class="card-head">
        <h2>Showing ${from}–${d.offset + d.count}</h2>
        <span class="small muted">one row per master hotel — open one to see its provider records</span>
        <div class="spacer"></div>
        ${exportBtn('mappings', 'Export all mappings')}</div>
        <div class="card-body flush"><div class="table-wrap"><table class="results">
          <thead><tr>
            <th>#</th><th>Master ID</th><th>Hotel Name</th><th>Address</th>
            <th>Providers</th><th class="num">Star</th>
            <th class="num">Lat</th><th class="num">Long</th><th>View</th><th>Find Duplicate</th>
          </tr></thead>
          <tbody>${d.results.map((r, i) => `<tr>
            <td class="faint">${d.offset + i + 1}</td>
            <td class="mono">${esc(r.master_id || '—')}</td>
            <td><a href="#/masters/${esc(r.master_id)}">${esc(r.hotel_name || '—')}</a></td>
            <td class="addr">${esc(r.address || '—')}${r.city ? ', ' + esc(r.city) : ''}${r.postal_code ? ', ' + esc(r.postal_code) : ''}</td>
            <td>
              <span class="chip chip-medium">${fmt(r.provider_count)}</span>
              <div class="small muted" style="margin-top:3px">${esc(r.providers || '—')}</div>
            </td>
            <td class="num">${r.star_rating ?? '—'}</td>
            <td class="num mono">${r.latitude ?? '—'}</td>
            <td class="num mono">${r.longitude ?? '—'}</td>
            <td><button class="icon-btn" title="View the ${fmt(r.provider_count)} provider record(s) behind this master"
                 onclick="location.hash='#/masters/${esc(r.master_id)}'">👁</button></td>
            <td><button class="icon-btn" title="Find possible duplicates"
                 onclick="App.findDuplicates(${r.supplier_hotel_row_id})">⧉</button></td>
          </tr>`).join('')}</tbody>
        </table></div></div>
        <div class="card-head" style="border-top:1px solid var(--border);border-bottom:none">
          <div class="spacer"></div>
          <button class="btn-sm" ${d.offset === 0 ? 'disabled' : ''}
            onclick="App.runSearch(${Math.max(0, d.offset - d.limit)})">Previous</button>
          <button class="btn-sm" ${d.has_more ? '' : 'disabled'}
            onclick="App.runSearch(${d.offset + d.limit})">Next</button>
        </div>
      </div>`;
  },

  async propertyCount() {
    const f = this.readFilters();
    el('countOut').textContent = 'Counting…';
    try {
      const c = await get(`${API}/masters/count?${this.filterQuery(f)}`);
      el('countOut').innerHTML = `<strong>${fmt(c.properties)}</strong> unique hotels ·
        ${fmt(c.supplier_records)} provider records · ${c.providers} provider(s)`;
    } catch (e) { el('countOut').textContent = ''; toast(e.message, true); }
  },

  resetFilters() {
    this.state.filters = {}; this.state.lastResults = null;
    this.refresh();
  },

  async findDuplicates(rowId) {
    showModal('Possible duplicates', '<div class="spinner">Looking…</div>', [{ label: 'Close', fn: closeModal }]);
    try {
      const d = await get(`${API}/records/${rowId}/duplicates`);
      const body = d.count ? `
        <p class="small muted">Hotels within 1 km with a similar name that sit under a
          <strong>different</strong> master. Open one to compare, or split if they were wrongly merged.</p>
        <div class="table-wrap"><table>
          <thead><tr><th>Master ID</th><th>Provider</th><th>Hotel</th><th class="num">Name match</th><th class="num">Distance</th></tr></thead>
          <tbody>${d.candidates.map(c => `<tr>
            <td class="mono"><a href="#/masters/${esc(c.master_id)}" onclick="closeModal()">${esc(c.master_id || '—')}</a></td>
            <td>${esc(c.provider_name)}</td>
            <td>${esc(c.hotel_name || '—')}<div class="faint small">${esc(c.city || '')}</div></td>
            <td class="num">${c.name_match_pct}%</td>
            <td class="num">${c.distance_meters} m</td>
          </tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">No likely duplicates found for this record.</div>';
      showModal('Possible duplicates', body, [{ label: 'Close', fn: closeModal }]);
    } catch (e) { closeModal(); toast(e.message, true); }
  },

  async renderMasterDetail(publicId) {
    const m = await get(`${API}/masters/${encodeURIComponent(publicId)}`);
    this.state.master = m;
    this.state.selected = new Set();

    const redirect = m.was_redirected
      ? `<div class="alert alert-info"><div class="alert-icon">→</div><div class="alert-body">
          <div class="alert-title">${esc(m.requested_public_id)} was merged into ${esc(m.public_id)}</div>
          <div class="alert-text">The old ID still works — anything holding it is redirected here.</div>
        </div></div>` : '';

    const deprecated = ['Deprecated', 'Dormant'].includes(m.status)
      ? `<div class="alert alert-warn"><div class="alert-icon">⚑</div><div class="alert-body">
          <div class="alert-title">This hotel is marked ${esc(m.status)}</div>
          <div class="alert-text">${esc(m.deprecation_reason || '')} — the ID still resolves but is not bookable.</div>
        </div>
        <button class="btn-sm" onclick="App.reactivateModal()">Reactivate</button></div>` : '';

    el('view').innerHTML = `
      ${redirect}${deprecated}
      <div class="card">
        <div class="card-head">
          <h2>${esc(m.hotel_name || '—')}</h2>
          <span class="chip chip-${(m.status || '').toLowerCase()}">${esc(m.status)}</span>
          <div class="spacer"></div>
          <button class="btn-sm" onclick="location.hash='#/masters'">← Back</button>
        </div>
        <div class="card-body">
          <div class="row">
            <div class="col"><dl class="kv">
              <dt>Public ID</dt><dd class="mono"><strong>${esc(m.public_id)}</strong></dd>
              <dt>Address</dt><dd>${esc(m.address || '—')}</dd>
              <dt>City</dt><dd>${esc(m.city || '—')}${m.state ? ', ' + esc(m.state) : ''}</dd>
              <dt>Postal code</dt><dd>${esc(m.postal_code || '—')}</dd>
              <dt>Coordinates</dt><dd class="mono">${m.latitude ?? '—'}, ${m.longitude ?? '—'}</dd>
            </dl></div>
            <div class="col">
              <p class="small muted">This master groups ${m.members.length} supplier record(s) believed to be the same
                physical hotel. If any of them is a <strong>different</strong> hotel, select it below and split it out —
                that decision is permanent and the matching engine will never re-merge them.</p>
              <div class="toolbar" style="margin-top:10px">
                <button class="btn-danger btn-sm" onclick="App.splitModal()">Split selected out</button>
                <button class="btn-sm" onclick="App.deprecateModal()">Mark closed</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Supplier records in this master</h2></div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th></th><th>Hotel name</th><th>Supplier</th><th>Supplier ID</th>
            <th class="num">Name match</th><th class="num">Distance</th><th>Confidence</th></tr></thead>
          <tbody>${m.members.map(r => `<tr id="mem-${r.supplier_hotel_row_id}">
            <td><input type="checkbox" class="mem" value="${r.supplier_hotel_row_id}"
                 onchange="App.toggleMember(${r.supplier_hotel_row_id}, this.checked)"></td>
            <td><strong>${esc(r.hotel_name || '—')}</strong><div class="faint small">${esc(r.address || '')}</div></td>
            <td>${esc(r.supplier_name)}</td>
            <td class="mono">${esc(r.supplier_hotel_id)}</td>
            <td class="num">${r.name_similarity != null ? Number(r.name_similarity).toFixed(0) + '%' : '—'}</td>
            <td class="num">${r.distance_meters != null ? Number(r.distance_meters).toFixed(0) + ' m' : '—'}</td>
            <td><span class="chip chip-${(r.confidence_tier || 'tier1').toLowerCase()}">${esc(r.confidence_tier || 'SEED')}</span></td>
          </tr>`).join('')}</tbody>
        </table></div></div>
      </div>

      <div class="card"><div class="card-head"><h2>History</h2></div>
        <div class="card-body">${m.history.length ? `<ul class="timeline">${m.history.map(h => `<li>
          <strong>${esc(h.event)}</strong>${h.related_id ? ` → <span class="mono">${esc(h.related_id)}</span>` : ''}
          <div class="muted small">${esc(h.detail || '')}</div>
          <div class="faint small">${new Date(h.created_at).toLocaleString()}</div>
        </li>`).join('')}</ul>` : '<div class="muted">No changes recorded.</div>'}</div>
      </div>`;
  },

  toggleMember(id, on) {
    on ? this.state.selected.add(id) : this.state.selected.delete(id);
    const row = el(`mem-${id}`);
    if (row) row.classList.toggle('selected', on);
  },

  splitModal() {
    const sel = [...this.state.selected];
    const m = this.state.master;
    if (!sel.length) return toast('Tick the records that are a different hotel', true);
    if (sel.length >= m.members.length) return toast('You cannot move every record — that is not a split', true);

    const moving = m.members.filter(x => sel.includes(x.supplier_hotel_row_id));
    const staying = m.members.filter(x => !sel.includes(x.supplier_hotel_row_id));

    showModal('Split into a separate hotel', `
      <p class="small muted">These records will be moved into a brand-new master hotel with its own ID.
        ${esc(m.public_id)} keeps the rest.</p>
      <div class="row" style="margin:14px 0">
        <div class="col"><label>Moving out (${moving.length})</label>
          <ul class="small">${moving.map(x => `<li>${esc(x.hotel_name)} <span class="faint">(${esc(x.supplier_name)})</span></li>`).join('')}</ul></div>
        <div class="col"><label>Staying in ${esc(m.public_id)} (${staying.length})</label>
          <ul class="small">${staying.map(x => `<li>${esc(x.hotel_name)} <span class="faint">(${esc(x.supplier_name)})</span></li>`).join('')}</ul></div>
      </div>
      <div class="field"><label>Why are these different hotels? (recorded permanently)</label>
        <textarea id="splitReason" rows="3" placeholder="e.g. Novotel is a separate property 300m from the Hilton"></textarea></div>
      <div class="alert alert-info" style="margin:0"><div class="alert-icon">i</div><div class="alert-body">
        <div class="alert-text">The engine will be told never to merge these again. Anything holding
          <span class="mono">${esc(m.public_id)}</span> should re-check which hotel it meant.</div></div></div>
    `, [
      { label: 'Cancel', fn: closeModal },
      { label: 'Split them apart', cls: 'btn-danger', fn: () => App.doSplit(sel) },
    ]);
  },

  async doSplit(ids) {
    const reason = el('splitReason').value.trim();
    if (reason.length < 3) return toast('Please give a reason', true);
    try {
      const r = await post(`${API}/masters/${this.state.master.public_id}/split`,
        { supplier_row_ids: ids, reason });
      closeModal();
      toast(`Split complete — new hotel ${r.new_public_id} created`);
      location.hash = `#/masters/${r.new_public_id}`;
    } catch (e) { toast(e.message, true); }
  },

  deprecateModal() {
    showModal('Mark this hotel closed', `
      <p class="small muted">The ID keeps working — anything asking about it is told the hotel is closed,
        rather than getting an error. This can be undone.</p>
      <div class="field"><label>Which applies?</label>
        <select id="depCase">
          <option value="Closed">Closed — the property has shut down</option>
          <option value="Dormant">Dormant — still exists, but no supplier lists it right now</option>
        </select></div>
      <div class="field"><label>Reason</label>
        <textarea id="depReason" rows="3" placeholder="e.g. Property permanently closed, confirmed with supplier"></textarea></div>
    `, [
      { label: 'Cancel', fn: closeModal },
      { label: 'Mark closed', cls: 'btn-danger', fn: () => App.doDeprecate() },
    ]);
  },

  async doDeprecate() {
    const reason = el('depReason').value.trim();
    if (reason.length < 3) return toast('Please give a reason', true);
    try {
      await post(`${API}/masters/${this.state.master.public_id}/deprecate`,
        { reason, case: el('depCase').value });
      closeModal(); toast('Hotel marked closed'); this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  reactivateModal() {
    showModal('Reactivate this hotel', `
      <div class="field"><label>Reason</label>
        <textarea id="reReason" rows="3" placeholder="e.g. Property reopened under the same name"></textarea></div>
    `, [
      { label: 'Cancel', fn: closeModal },
      { label: 'Reactivate', cls: 'btn-primary', fn: () => App.doReactivate() },
    ]);
  },

  async doReactivate() {
    const reason = el('reReason').value.trim();
    if (reason.length < 3) return toast('Please give a reason', true);
    await post(`${API}/masters/${this.state.master.public_id}/reactivate`, { reason });
    closeModal(); toast('Hotel reactivated'); this.refresh();
  },

  /* ── Manual review ──────────────────────────────────────────────────── */

  async render_review() {
    const q = this.state.reviewQuery || '';
    const d = await get(`${API}/review-queue/search?${q ? `q=${encodeURIComponent(q)}&` : ''}limit=200`);

    el('view').innerHTML = `
      <div class="card"><div class="card-body">
        <div class="toolbar">
          <div class="grow"><input type="search" id="rq" value="${esc(q)}"
            placeholder="Search any field — hotel name, provider, hotel ID, city, address, postcode, suggested match, or reason"></div>
          <button class="btn-primary" onclick="App.reviewSearch()">Search</button>
          ${q ? `<button onclick="App.clearReviewSearch()">Clear</button>` : ''}
        </div>
      </div></div>

      ${d.count ? `
      <div class="card"><div class="card-head">
        <h2>${d.count} hotel(s)${q ? ` matching “${esc(q)}”` : ' awaiting a decision'}</h2>
        <span class="muted small" id="revCount"></span>
        <div class="spacer"></div>
        <button class="btn-sm btn-primary" id="revApprove" disabled
                onclick="App.reviewBatch('approve')">Approve selected</button>
        <button class="btn-sm" id="revReject" disabled
                onclick="App.reviewBatch('reject')">Reject selected</button>
        ${exportBtn('manual-review')}</div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr>
            <th style="width:28px"><input type="checkbox" id="revAll" onclick="App.reviewSelectAll(this.checked)"></th>
            <th>Supplier hotel</th><th>Provider</th><th>Provider Hotel Id</th>
            <th>City</th><th>Suggested match</th>
            <th class="num">Confidence</th><th>Why</th><th>Action</th></tr></thead>
          <tbody>${d.results.map(x => `<tr>
            <td><input type="checkbox" class="rev-pick" value="${x.supplier_hotel_row_id}"
                 onclick="App.reviewSelectionChanged()"></td>
            <td><strong>${esc(x.hotel_name || '—')}</strong><div class="faint small">${esc(x.address || '')}</div></td>
            <td>${esc(x.supplier_name)}</td>
            <td class="mono">${esc(x.supplier_hotel_id)}</td>
            <td>${esc(x.city || '—')}</td>
            <td>${x.master_public_id
                  ? `<a href="#/masters/${esc(x.master_public_id)}">${esc(x.master_hotel_name || '—')}</a>`
                  : esc(x.master_hotel_name || '—')}</td>
            <td class="num">${x.rule_score ?? x.ai_similarity ?? '—'}
              <div class="faint small">${reviewKind(x.review_type)}</div></td>
            <td class="muted small reason" title="${esc(x.decision_reason || '')}">${esc(x.decision_reason || '')}</td>
            <td><button class="btn-sm btn-primary"
                 onclick="App.openAttach(${x.supplier_hotel_row_id})">Attach to master…</button></td>
          </tr>`).join('')}</tbody>
        </table></div></div></div>`
      : q ? `<div class="empty">Nothing in the review queue matches “${esc(q)}”.</div>`
          : `<div class="alert alert-ok"><div class="alert-icon">✓</div><div class="alert-body">
              <div class="alert-title">Nothing waiting</div>
              <div class="alert-text">Every hotel was resolved automatically. Borderline cases the AI
                rejects outright now become their own hotel rather than queueing here.</div></div></div>`}`;

    const box = el('rq');
    if (box) {
      box.onkeydown = e => { if (e.key === 'Enter') this.reviewSearch(); };
      box.oninput = () => {
        clearTimeout(this.state.reviewTimer);
        this.state.reviewTimer = setTimeout(() => this.reviewSearch(), 350);
      };
    }
  },

  reviewSearch() {
    const box = el('rq');
    const next = box ? box.value.trim() : '';
    if (next === (this.state.reviewQuery || '')) return;
    this.state.reviewQuery = next;
    this.refresh();
  },

  clearReviewSearch() { this.state.reviewQuery = ''; this.refresh(); },

  /* ── Batch decisions on the review queue ────────────────────────────────── */

  reviewPicked() {
    return [...document.querySelectorAll('.rev-pick:checked')].map(b => Number(b.value));
  },

  reviewSelectAll(on) {
    document.querySelectorAll('.rev-pick').forEach(b => { b.checked = on; });
    this.reviewSelectionChanged();
  },

  reviewSelectionChanged() {
    const n = this.reviewPicked().length;
    const label = el('revCount');
    if (label) label.textContent = n ? `${n} selected` : '';
    ['revApprove', 'revReject'].forEach(id => {
      const btn = el(id);
      if (btn) btn.disabled = n === 0;
    });
  },

  async reviewBatch(action) {
    const ids = this.reviewPicked();
    if (!ids.length) return;

    // Names the consequence rather than asking "are you sure?". Approve
    // attaches each record to the master already suggested for it; reject
    // records a permanent decision that those two are different hotels, which
    // the next run will not undo.
    const what = action === 'approve'
      ? `Attach ${fmt(ids.length)} record(s) to the master suggested for each?`
      : `Record ${fmt(ids.length)} record(s) as NOT matching their suggested master?
         This is permanent — the matching engine will never re-suggest these pairs.`;

    showModal(action === 'approve' ? 'Approve selected' : 'Reject selected',
      `<p>${esc(what)}</p>
       <p class="small muted">Only the ${fmt(ids.length)} row(s) you ticked are affected.</p>`,
      [
        { label: 'Cancel', fn: () => closeModal() },
        {
          label: action === 'approve' ? 'Approve' : 'Reject',
          cls: action === 'approve' ? 'btn-primary' : 'btn-danger',
          fn: () => this.confirmReviewBatch(action, ids),
        },
      ]);
  },

  async confirmReviewBatch(action, ids) {
    try {
      const r = await post(`${API}/manual-review/batch`, {
        supplier_hotel_ids: ids, action,
      });
      closeModal();
      toast(r.failed
        ? `${fmt(r.applied)} applied, ${fmt(r.failed)} could not be — they stay in the queue`
        : `${fmt(r.applied)} record(s) ${action === 'approve' ? 'approved' : 'rejected'}`,
        r.failed > 0);
      this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  /* ── Attach a queued record to a master the reviewer chooses ────────────── */

  async openAttach(rowId, query) {
    let d;
    try {
      d = await get(`${API}/manual-review/${rowId}/master-candidates?limit=20`
        + (query ? `&q=${encodeURIComponent(query)}` : ''));
    } catch (e) { return toast(e.message, true); }

    const s = d.source || {};
    const rows = d.candidates.length ? d.candidates.map(c => `
      <tr${c.is_suggested ? ' class="selected"' : ''}>
        <td>
          <strong>${esc(c.hotel_name || '—')}</strong>
          ${c.is_suggested ? '<span class="chip chip-new" style="margin-left:6px">suggested</span>' : ''}
          ${c.status && c.status !== 'Active' ? `<span class="chip chip-dormant" style="margin-left:4px">${esc(c.status)}</span>` : ''}
          <div class="faint small">${esc(c.address || '')}${c.postal_code ? ', ' + esc(c.postal_code) : ''}</div>
          <div class="mono faint small">${esc(c.public_id || '—')}</div>
        </td>
        <td class="num">${c.name_match_pct ?? '—'}%</td>
        <td class="num">${c.distance_meters != null ? fmt(c.distance_meters) + ' m' : '—'}</td>
        <td class="num">${fmt(c.provider_count)}</td>
        <td><button class="btn-sm btn-primary"
             onclick="App.doAttach(${rowId}, ${c.master_hotel_id})">Attach</button></td>
      </tr>`).join('')
      : `<tr><td colspan="5" class="muted" style="padding:14px">
           ${d.searched ? 'No master matches that search.' : 'No master hotels nearby — search by name instead.'}
         </td></tr>`;

    showModal(`Attach “${s.hotel_name || ''}” to a master`, `
      <p class="small muted">
        ${esc(s.supplier_name || '')} ${esc(s.supplier_hotel_id || '')} ·
        ${esc(s.address || '')}${s.city ? ', ' + esc(s.city) : ''}
      </p>
      <div class="toolbar" style="margin:10px 0">
        <div class="grow"><input type="search" id="attachQ" value="${esc(query || '')}"
          placeholder="Search all masters by name or city — otherwise showing the nearest"></div>
        <button class="btn-sm" onclick="App.attachSearch(${rowId})">Search</button>
        ${query ? `<button class="btn-sm" onclick="App.openAttach(${rowId})">Nearest</button>` : ''}
      </div>
      <div class="table-wrap" style="max-height:340px;overflow-y:auto">
        <table>
          <thead><tr><th>Master hotel</th><th class="num">Name</th>
            <th class="num">Distance</th><th class="num">Providers</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="small muted" style="margin-top:10px">
        Attaches this provider record to the chosen hotel and closes the review.
        Choosing something other than the suggestion is recorded, so the thresholds
        can be re-fitted against real decisions later.
      </p>
    `, [{ label: 'Cancel', fn: () => closeModal() }]);

    const box = el('attachQ');
    if (box) box.onkeydown = e => { if (e.key === 'Enter') this.attachSearch(rowId); };
  },

  attachSearch(rowId) {
    const box = el('attachQ');
    this.openAttach(rowId, box ? box.value.trim() : '');
  },

  async doAttach(rowId, masterId) {
    try {
      const r = await post(`${API}/manual-review/${rowId}/attach`, { master_hotel_id: masterId });
      closeModal();
      toast(`Attached to ${r.public_id} — ${r.master_hotel_name}`
        + (r.agreed_with_suggestion ? '' : ' (overruled the suggestion)'));
      this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  /* ── Pending (provisional) masters ──────────────────────────────────── */

  async render_provisional() {
    const suspiciousOnly = !!this.state.provSuspiciousOnly;
    const [summary, d] = await Promise.all([
      get(`${API}/provisional/summary`),
      get(`${API}/provisional?limit=200${suspiciousOnly ? '&suspicious_only=true' : ''}`),
    ]);

    // Handlers look rows up by index. Interpolating a hotel name into an
    // onclick attribute breaks on the first apostrophe in "Raj's Inn".
    this.state.provRows = d.results;

    el('view').innerHTML = `
      <div class="alert alert-${summary.with_exact_name_neighbour ? 'info' : 'ok'}">
        <div class="alert-icon">${summary.with_exact_name_neighbour ? 'i' : '✓'}</div>
        <div class="alert-body">
          <div class="alert-title">${fmt(summary.provisional_masters)} hotel(s) claimed by only one provider</div>
          <div class="alert-text">A hotel only one provider lists has nothing to confirm it exists, so it is
            held back from every export until a second provider sends the same hotel — which happens
            automatically — or you confirm it here.
            ${summary.with_exact_name_neighbour
              ? `<strong>${fmt(summary.with_exact_name_neighbour)}</strong> of them have a same-named hotel nearby
                 and are probably a hotel you already have. Those are listed first.`
              : 'None of them look like a hotel you already have.'}</div>
        </div></div>

      <div class="card"><div class="card-body">
        <div class="toolbar">
          <label style="display:flex;align-items:center;gap:6px;margin:0">
            <input type="checkbox" id="provSusp" ${suspiciousOnly ? 'checked' : ''}
              onchange="App.toggleProvSuspicious()"> Show only likely duplicates</label>
          <div class="spacer"></div>
          <span class="muted small">${d.count} shown</span>
        </div>
      </div></div>

      ${d.count ? `
      <div class="card"><div class="card-body flush"><div class="table-wrap"><table>
        <thead><tr><th>Hotel</th><th>City</th><th>Provider</th>
          <th class="num">Look-alikes nearby</th><th>Decide</th></tr></thead>
        <tbody>${d.results.map((r, i) => `<tr>
          <td><strong>${esc(r.hotel_name || '—')}</strong>
            <div class="faint small">${esc(r.address || '')}</div>
            <div class="faint small mono">${esc(r.public_id)}</div></td>
          <td>${esc(r.city || '—')}</td>
          <td>${esc(r.providers || '—')}</td>
          <td class="num">${r.suspicion > 0
              ? `<span class="nav-badge">${r.suspicion}</span>` : '—'}</td>
          <td>
            <button class="btn-sm ${r.suspicion > 0 ? 'btn-primary' : ''}"
              onclick="App.showSuggestions('${esc(r.public_id)}')">Find its master</button>
            <button class="btn-sm" onclick="App.confirmProvisional(${i})">
              It is its own hotel</button>
          </td>
        </tr>`).join('')}</tbody>
      </table></div></div></div>`
      : `<div class="empty">${suspiciousOnly
          ? 'No single-provider hotels look like duplicates.'
          : 'Nothing waiting — every hotel has been confirmed by a second provider or a reviewer.'}</div>`}`;
  },

  toggleProvSuspicious() {
    this.state.provSuspiciousOnly = el('provSusp').checked;
    this.refresh();
  },

  async showSuggestions(publicId) {
    let d;
    try { d = await get(`${API}/provisional/${encodeURIComponent(publicId)}/suggestions`); }
    catch (e) { return toast(e.message, true); }

    if (!d.count) return toast('No look-alikes found for this hotel', true);

    showModal(`Which hotel is this?`, `
      <p class="small muted">These are already in the master list and look like the same property.
        Choosing one merges this record into it — the merged ID keeps working for anyone who cached it.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Hotel</th><th>Providers</th><th class="num">Apart</th>
          <th class="num">Name match</th><th></th></tr></thead>
        <tbody>${d.suggestions.map(s => `<tr>
          <td><strong>${esc(s.hotel_name || '—')}</strong>
            <div class="faint small">${esc(s.address || '')} ${esc(s.city || '')}</div>
            <div class="faint small mono">${esc(s.public_id)}</div></td>
          <td class="small">${esc(s.providers || '—')}</td>
          <td class="num">${fmt(s.distance_meters)} m</td>
          <td class="num">${s.strict_name_match ? '<strong>exact</strong>'
              : s.core_name_match ? 'exact (minus city)' : `${s.name_match_pct}%`}</td>
          <td><button class="btn-sm btn-primary"
            onclick="App.doMerge('${esc(publicId)}','${esc(s.public_id)}')">This one</button></td>
        </tr>`).join('')}</tbody>
      </table></div>`,
      [{ label: 'Close', fn: closeModal }]);
  },

  confirmProvisional(index) {
    const row = this.state.provRows?.[index];
    if (!row) return;
    const publicId = row.public_id;
    showModal('Confirm this hotel', `
      <p>Publish <strong>${esc(row.hotel_name || publicId)}</strong> as a hotel in its own right?</p>
      <p class="small muted">Do this when it is a real property that only one provider happens to sell.
        It will start appearing in exports.</p>
      <div class="field"><label>Why (kept on the record)</label>
        <input type="text" id="confirmReason" value="Genuine single-provider property"></div>`,
      [{ label: 'Cancel', fn: closeModal },
       { label: 'Confirm hotel', cls: 'btn-primary', fn: () => App.doConfirm(publicId) }]);
  },

  async doConfirm(publicId) {
    const reason = el('confirmReason')?.value.trim() || 'Confirmed by reviewer';
    closeModal();
    try {
      await post(`${API}/provisional/${encodeURIComponent(publicId)}/confirm`, { reason });
      toast('Confirmed — it will appear in exports');
      this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  async doMerge(fromId, intoId, reason) {
    closeModal();
    try {
      const r = await post(`${API}/masters/${encodeURIComponent(fromId)}/merge`, {
        into: intoId,
        reason: reason || 'Reviewer confirmed these are the same hotel',
      });
      let msg = `Merged into ${r.surviving_public_id} — ${r.records_moved} record(s) moved`;
      if (r.overlapping_suppliers?.length)
        msg += `. Note: ${r.overlapping_suppliers.join(', ')} now appear(s) twice on this hotel.`;
      toast(msg);
      this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  /* ── Duplicate masters (fragmentation) ──────────────────────────────── */

  /* ── Known splits (from the reference mapping) ──────────────────────────── */

  async render_splits() {
    const d = await get(`${API}/evaluation/splits?limit=400`);
    this.state.splits = d.splits;

    if (!d.count) {
      el('view').innerHTML = `<div class="alert alert-ok"><div class="alert-icon">✓</div>
        <div class="alert-body"><div class="alert-title">Nothing split</div>
        <div class="alert-text">Every hotel in the reference mapping sits in exactly one master,
          or no reference mapping is loaded.</div></div></div>`;
      return;
    }

    const queued = d.splits.filter(x => x.in_review).length;

    el('view').innerHTML = `
      <div class="alert alert-info"><div class="alert-icon">i</div><div class="alert-body">
        <div class="alert-title">${fmt(d.count)} hotel(s) are split across more than one master</div>
        <div class="alert-text">These are not guesses. The reference mapping says each row below is
          one hotel, and we filed it under several — so every merge here is a known correction, not a
          judgement call. Merging one raises measured recall.
          ${queued ? `${fmt(queued)} already have a record waiting in Manual Review.` : ''}
          Unlike Duplicate Masters, this finds them even when the names differ and they sit far apart.</div>
      </div></div>

      <div class="card"><div class="card-body flush"><div class="table-wrap"><table>
        <thead><tr><th style="width:56px" class="num">Masters</th><th>Our masters</th>
          <th>Also queued</th><th>Merge</th></tr></thead>
        <tbody>${d.splits.map((x, i) => `<tr>
          <td class="num"><strong>${x.masters}</strong></td>
          <td>${(x.masters_detail || []).map((m, j) => `
            <div style="${j ? 'margin-top:5px' : ''}">
              <strong>${esc(m.hotel_name || '—')}</strong>
              <span class="chip ${m.status === 'Active' ? 'chip-active' : 'chip-dormant'}"
                    style="margin-left:5px">${esc(m.status || '—')}</span>
              <div class="faint small mono">${esc(m.public_id || '(no id)')} · ${m.providers} provider(s)${m.city ? ' · ' + esc(m.city) : ''}</div>
            </div>`).join('')}</td>
          <td class="small muted">${x.in_review ? 'in Manual Review' : '—'}</td>
          <td><button class="btn-sm btn-primary" onclick="App.confirmSplitMerge(${i})">Merge…</button></td>
        </tr>`).join('')}</tbody>
      </table></div></div></div>`;
  },

  confirmSplitMerge(index) {
    const x = this.state.splits?.[index];
    if (!x || !x.masters_detail?.length) return;

    // Already ordered by provider count, so the first holds the most records
    // and folding the rest into it moves the fewest.
    const [keep, ...fold] = x.masters_detail;
    const foldable = fold.filter(m => m.public_id);

    if (!keep.public_id || !foldable.length) {
      return toast('These masters have no public IDs to merge', true);
    }

    showModal('Merge into one hotel?', `
      <p>Keep <strong>${esc(keep.hotel_name || keep.public_id)}</strong>
         <span class="mono faint">(${esc(keep.public_id)}, ${keep.providers} provider(s))</span>
         and fold in:</p>
      <ul class="small">${foldable.map(m =>
        `<li>${esc(m.hotel_name || '')} <span class="mono faint">(${esc(m.public_id)}, ${m.providers} provider(s))</span></li>`
      ).join('')}</ul>
      <p class="small muted">All provider records move across. Each folded ID keeps resolving to the
        survivor, so nothing that cached one breaks.</p>
    `, [
      { label: 'Cancel', fn: () => closeModal() },
      { label: `Merge ${foldable.length + 1} into 1`, cls: 'btn-primary',
        fn: () => this.doSplitMerge(keep, foldable) },
    ]);
  },

  async doSplitMerge(keep, foldable) {
    let merged = 0;
    const failures = [];

    for (const m of foldable) {
      try {
        await post(`${API}/masters/${encodeURIComponent(m.public_id)}/merge`, {
          into: keep.public_id,
          reason: 'Reference mapping records these as one hotel',
        });
        merged++;
      } catch (e) { failures.push(`${m.public_id}: ${e.message}`); }
    }

    closeModal();
    toast(failures.length
      ? `${merged} merged, ${failures.length} failed — ${failures[0]}`
      : `Merged ${merged} master(s) into ${keep.public_id}`, failures.length > 0);
    this.refresh();
  },

  async render_duplicates() {
    const d = await get(`${API}/fragmentation?limit=400`);
    const strict = d.pairs.filter(p => p.match_class === 'STRICT').length;
    this.state.dupPairs = d.pairs;

    el('view').innerHTML = `
      <div class="alert alert-${d.count ? 'info' : 'ok'}">
        <div class="alert-icon">${d.count ? 'i' : '✓'}</div><div class="alert-body">
        <div class="alert-title">${d.count ? `${fmt(d.count)} pair(s) look like the same hotel listed twice` : 'No duplicate hotels found'}</div>
        <div class="alert-text">Two masters that are almost certainly one hotel — either the same name close
          together, or the same postcode and building number even when the coordinates are far apart
          (a supplier's GPS can be kilometres wrong, which distance alone can never catch).
          ${strict ? `${fmt(strict)} match on the full name.` : ''}
          New imports are now held for review instead of splitting like this — these are the ones from before.</div>
        </div></div>

      ${d.count ? `
      <div class="card"><div class="card-body flush"><div class="table-wrap"><table>
        <thead><tr><th>Hotel A</th><th>Hotel B</th><th class="num">Apart</th>
          <th>Evidence</th><th>Merge</th></tr></thead>
        <tbody>${d.pairs.map((p, i) => `<tr>
          <td><strong>${esc(p.hotel_name_a || '—')}</strong>
            <div class="faint small">${esc(p.address_a || '')} · ${esc(p.city_a || '')}</div>
            <div class="faint small mono">${esc(p.master_id_a)} · ${p.providers_a} provider(s)${p.status_a === 'Provisional' ? ' · pending' : ''}</div></td>
          <td><strong>${esc(p.hotel_name_b || '—')}</strong>
            <div class="faint small">${esc(p.address_b || '')} · ${esc(p.city_b || '')}</div>
            <div class="faint small mono">${esc(p.master_id_b)} · ${p.providers_b} provider(s)${p.status_b === 'Provisional' ? ' · pending' : ''}</div></td>
          <td class="num">${fmt(p.distance_meters)} m${p.match_class === 'SAME_ADDRESS' && p.distance_meters > 1000 ? '<div class="faint">likely bad coordinate</div>' : ''}</td>
          <td class="small">${
            p.match_class === 'STRICT' ? 'Name matches exactly'
            : p.match_class === 'SAME_ADDRESS' ? 'Same postcode &amp; building number'
            : 'Name matches once city removed'}
            ${p.postal_agrees ? '<div class="faint">Postcode agrees</div>' : '<div class="faint">Postcode differs</div>'}</td>
          <td><button class="btn-sm btn-primary" onclick="App.confirmPairMerge(${i})">Merge…</button></td>
        </tr>`).join('')}</tbody>
      </table></div></div></div>` : ''}`;
  },

  confirmPairMerge(index) {
    const p = this.state.dupPairs?.[index];
    if (!p) return;
    // Keep the one with more providers; on a tie the older id wins anyway.
    const keepA = (p.providers_a >= p.providers_b);
    const keep = keepA ? p : { ...p, master_id_a: p.master_id_b, master_id_b: p.master_id_a,
                               hotel_name_a: p.hotel_name_b, hotel_name_b: p.hotel_name_a };

    showModal('Merge these two hotels?', `
      <p>Keep <strong>${esc(keep.hotel_name_a || keep.master_id_a)}</strong>
         <span class="mono faint">(${esc(keep.master_id_a)})</span>
         and fold <strong>${esc(keep.hotel_name_b || keep.master_id_b)}</strong>
         <span class="mono faint">(${esc(keep.master_id_b)})</span> into it.</p>
      <p class="small muted">They are ${fmt(p.distance_meters)} m apart.
        All provider records move across. The folded ID keeps resolving, so nothing that cached it breaks.
        If this turns out to be wrong you can split them again.</p>
      <div class="field"><label>Why (kept on the record permanently)</label>
        <input type="text" id="mergeReason" value="Same hotel — providers disagree on the coordinates"></div>`,
      [{ label: 'Cancel', fn: closeModal },
       { label: 'Merge', cls: 'btn-primary',
         fn: () => App.doMergeWithReason(keep.master_id_b, keep.master_id_a) }]);
  },

  doMergeWithReason(fromId, intoId) {
    const reason = el('mergeReason')?.value.trim() || 'Reviewer confirmed these are the same hotel';
    this.doMerge(fromId, intoId, reason);
  },

  /* ── Integrity ──────────────────────────────────────────────────────── */

  async render_integrity() {
    const r = await get(`${API}/integrity`);
    const collisions = r.same_supplier_collision_count;

    el('view').innerHTML = `
      <div class="alert alert-${collisions ? 'danger' : 'ok'}">
        <div class="alert-icon">${collisions ? '!' : '✓'}</div><div class="alert-body">
        <div class="alert-title">${collisions ? `${collisions} probable wrong merge(s) found` : 'No wrong merges detected'}</div>
        <div class="alert-text">A supplier never lists one hotel twice under different names. If one master holds
          two records from the same supplier, they are almost certainly different hotels.
          This is checked on <strong>every</strong> mapping, not a sample.</div></div></div>

      ${collisions ? `<div class="card"><div class="card-head"><h2>Same-supplier conflicts</h2></div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Master</th><th>Supplier</th><th>Conflicting names</th><th></th></tr></thead>
          <tbody>${r.same_supplier_collisions.map(c => `<tr>
            <td>${esc(c.master_hotel_name || '—')}</td>
            <td>${esc(c.supplier_name)}</td>
            <td class="small">${esc(c.names || '')}</td>
            <td><button class="btn-sm" onclick="App.openMasterByInternal(${c.master_hotel_id})">Inspect</button></td>
          </tr>`).join('')}</tbody></table></div></div></div>` : ''}

      <div class="card"><div class="card-head"><h2>Masters worth a look (${r.absorbing_master_count})</h2>
        <div class="spacer"></div>${exportBtn('integrity')}</div>
        <div class="card-body">
          <p class="small muted" style="margin-top:0">Records spread unusually far apart, or with weak name agreement.
            A wide spread with identical names is normally just suppliers disagreeing on coordinates — not an error.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Hotel</th><th>City</th><th class="num">Records</th><th class="num">Spread</th><th class="num">Worst name match</th><th></th></tr></thead>
            <tbody>${r.absorbing_masters.slice(0, 60).map(a => `<tr>
              <td><strong>${esc(a.hotel_name || '—')}</strong></td>
              <td>${esc(a.city || '—')}</td>
              <td class="num">${a.mapping_count}</td>
              <td class="num">${a.max_spread_meters} m</td>
              <td class="num">${a.worst_name_similarity ?? '—'}</td>
              <td><button class="btn-sm" onclick="App.openMasterByInternal(${a.master_hotel_id})">Inspect</button></td>
            </tr>`).join('')}</tbody>
          </table></div>
        </div></div>`;
  },

  async openMasterByInternal(internalId) {
    const r = await get(`${API}/masters/search?q=${internalId}&limit=1`).catch(() => null);
    // fall back to a direct lookup path via search on name
    toast('Opening master…');
    const d = await get(`${API}/dashboard`);
    location.hash = '#/masters';
  },

  /* ── downloads ──────────────────────────────────────────────────────── */

  download(dataset) {
    toast('Preparing your Excel file…');
    window.location = `${API}/export/${dataset}`;
  },

  /* ── Import ─────────────────────────────────────────────────────────── */

  async render_import() {
    const mode = this.state.importMode || 'file';
    el('view').innerHTML = `
      <div class="card"><div class="card-head">
        <h2>Import supplier hotels</h2><div class="spacer"></div>
        <button class="btn-sm ${mode === 'file' ? 'btn-primary' : ''}" onclick="App.setImportMode('file')">From a file</button>
        <button class="btn-sm ${mode === 'db' ? 'btn-primary' : ''}" onclick="App.setImportMode('db')">From a database</button>
      </div>
      <div class="card-body">${mode === 'file' ? `
        <div class="row">
          <div class="col"><div class="field"><label>Supplier name</label>
            <input type="text" id="impSupplier" placeholder="e.g. Sabre">
            <p class="small faint" style="margin:4px 0 0">Leave blank if the workbook has one tab per supplier.</p></div></div>
          <div class="col"><div class="field"><label>CSV or Excel file</label>
            <input type="file" id="impFile" accept=".csv,.xlsx,.xls"></div></div>
        </div>
        <button class="btn-primary" onclick="App.analyseFile()">Check the file</button>
        <button onclick="App.analyseWorkbook()">All tabs — one supplier per tab</button>
        <p class="small muted" style="margin-bottom:0">Nothing is saved yet — you will see which
          column is which, and what the data looks like, before importing.
          If your workbook holds every supplier on its own tab, use the second button:
          all tabs are imported together and the pipeline is run once at the end.</p>
      ` : `
        <div class="field"><label>Supplier name</label>
          <input type="text" id="dbSupplier" placeholder="e.g. Sabre"></div>
        <div class="field"><label>Database connection</label>
          <input type="text" id="dbUrl" placeholder="postgresql://user:password@host:5432/dbname"></div>
        <div class="field"><label>Query (SELECT only)</label>
          <textarea id="dbQuery" rows="4" placeholder="SELECT hotel_id, name, address, city, country, lat, lon FROM hotels"></textarea></div>
        <button class="btn-primary" onclick="App.analyseDb()">Test and preview</button>
        <p class="small muted" style="margin-bottom:0">Only PostgreSQL and MySQL, read-only, single statement.</p>
      `}</div></div>
      <div id="importReport"></div>`;
  },

  setImportMode(mode) { this.state.importMode = mode; this.refresh(); },

  async analyseFile() {
    const supplier = el('impSupplier').value.trim();
    const file = el('impFile').files[0];
    if (!supplier) return toast('Enter a supplier name', true);
    if (!file) return toast('Choose a file', true);

    el('importReport').innerHTML = '<div class="spinner">Reading the file…</div>';
    const fd = new FormData();
    fd.append('supplier_name', supplier);
    fd.append('file', file);
    try {
      const r = await fetch(`${API}/import/file/analyse`, { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Could not read the file');
      this.state.importToken = d.token;
      this.state.importSupplier = supplier;
      this.renderImportReport(d, 'file');
    } catch (e) { el('importReport').innerHTML = ''; toast(e.message, true); }
  },

  async analyseWorkbook() {
    const file = el('impFile').files[0];
    if (!file) return toast('Choose a file', true);
    if (!/\.xlsx?$/i.test(file.name)) return toast('That button is for Excel workbooks — a CSV has only one sheet', true);

    el('importReport').innerHTML = '<div class="spinner">Reading every tab…</div>';
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch(`${API}/import/file/workbook`, { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Could not read the workbook');
      this.state.workbook = d;
      this.renderWorkbookReport(d);
    } catch (e) { el('importReport').innerHTML = ''; toast(e.message, true); }
  },

  renderWorkbookReport(d) {
    const dupes = d.already_present || 0;

    el('importReport').innerHTML = `
      ${dupes ? `<div class="alert alert-danger"><div class="alert-icon">!</div><div class="alert-body">
        <div class="alert-title">${fmt(dupes)} of these rows are already in the database</div>
        <div class="alert-text">This file has been imported before. Those rows will be
          <strong>skipped</strong>, not added again — only genuinely new records are imported.
          If you meant to replace the existing data, delete it first; importing does not overwrite.</div>
        </div></div>` : ''}

      <div class="alert alert-${d.importable === d.sheet_count ? 'ok' : 'info'}">
        <div class="alert-icon">${d.importable === d.sheet_count ? '✓' : 'i'}</div><div class="alert-body">
        <div class="alert-title">${d.sheet_count} tab(s) found · ${fmt(d.total_rows)} rows in total</div>
        <div class="alert-text">Each tab is imported as its own supplier. The name is taken from the tab —
          change it if it is wrong, because it is what every record is filed under.
          ${d.importable === d.sheet_count ? '' :
            `<strong>${d.sheet_count - d.importable}</strong> tab(s) cannot be imported and are shown below.`}
          Nothing is mapped until you run the pipeline, so all suppliers land first and are matched
          against each other in one go.</div></div></div>

      <div class="card"><div class="card-head"><h2>Tabs in this workbook</h2></div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Tab</th><th>Import as supplier</th><th class="num">Rows</th>
            <th class="num">Already there</th><th>Columns found</th><th>Include</th></tr></thead>
          <tbody>${d.sheets.map((s, i) => `<tr>
            <td class="mono">${esc(s.sheet)}</td>
            <td>${s.ok
                ? `<input type="text" id="wbName${i}" value="${esc(s.suggested_supplier_name)}" style="width:100%">`
                : '<span class="faint">—</span>'}</td>
            <td class="num">${fmt(s.total_rows || 0)}</td>
            <td class="num">${s.already_present
                ? `<span class="danger">${fmt(s.already_present)}</span>` : '—'}</td>
            <td class="small">${s.ok
                ? `${Object.keys(s.detected || {}).length} matched`
                : `<span class="danger">${esc(s.skip_reason || `missing ${(s.missing_required || []).join(', ')}`)}</span>`}</td>
            <td>${s.ok
                ? `<input type="checkbox" id="wbUse${i}" checked>`
                : '<span class="faint">cannot import</span>'}</td>
          </tr>`).join('')}</tbody>
        </table></div></div>
        <div class="card-head" style="border-top:1px solid var(--border);border-bottom:none">
          <button class="btn-primary" onclick="App.commitWorkbook()" ${d.importable ? '' : 'disabled'}>
            Import ${d.importable} tab(s)</button>
          <label style="display:flex;align-items:center;gap:6px;margin:0">
            <input type="checkbox" id="wbQueue" checked> Queue them all for mapping</label>
        </div>
      </div>
      <div id="wbProgress"></div>`;
  },

  async commitWorkbook() {
    const d = this.state.workbook;
    const enqueue = el('wbQueue').checked;

    const jobs = d.sheets
      .map((s, i) => ({ sheet: s, i }))
      .filter(({ sheet, i }) => sheet.ok && el(`wbUse${i}`)?.checked)
      .map(({ sheet, i }) => ({ sheet, name: el(`wbName${i}`).value.trim() }));

    if (!jobs.length) return toast('Select at least one tab', true);

    const blank = jobs.find(j => !j.name);
    if (blank) return toast(`Give tab "${blank.sheet.sheet}" a supplier name`, true);

    // Two tabs filed under one name would merge two feeds into one supplier,
    // which breaks the one-record-per-supplier rule the matcher relies on.
    const names = jobs.map(j => j.name.toLowerCase());
    const dupe = names.find((n, i) => names.indexOf(n) !== i);
    if (dupe) return toast(`Two tabs are both named "${dupe}" — give each supplier its own name`, true);

    const done = [];
    let failed = 0;

    for (const [n, job] of jobs.entries()) {
      el('wbProgress').innerHTML = `<div class="spinner">Importing ${esc(job.name)} (${n + 1} of ${jobs.length})…</div>`;
      try {
        const fd = new FormData();
        fd.append('supplier_name', job.name);
        fd.append('token', job.sheet.token);
        fd.append('enqueue', enqueue);
        const r = await fetch(`${API}/import/file/commit`, { method: 'POST', body: fd });
        const result = await r.json();
        if (!r.ok) throw new Error(result.detail || 'Import failed');
        done.push(result);
      } catch (e) {
        failed++;
        done.push({ supplier_name: job.name, inserted: 0, skipped: 0, error: e.message });
      }
    }

    const total = done.reduce((a, b) => a + (b.inserted || 0), 0);
    const queued = done.reduce((a, b) => a + (b.queued || 0), 0);

    el('wbProgress').innerHTML = `
      <div class="alert alert-${failed ? 'danger' : 'ok'}"><div class="alert-icon">${failed ? '!' : '✓'}</div>
        <div class="alert-body"><div class="alert-title">Imported ${fmt(total)} hotels across ${done.length - failed} supplier(s)</div>
        <div class="alert-text">${failed ? `${failed} tab(s) failed — see below.` : 'Every tab imported.'}
          ${done.reduce((a, b) => a + (b.already_present || 0), 0)
            ? `${fmt(done.reduce((a, b) => a + (b.already_present || 0), 0))} row(s) were already in the database and were skipped.` : ''}
          ${queued ? `${fmt(queued)} record(s) are queued and waiting.
            <strong>Run the pipeline now</strong> so all suppliers are matched against each other in one pass.` : ''}</div>
        </div></div>
      <div class="card"><div class="card-body flush"><div class="table-wrap"><table>
        <thead><tr><th>Supplier</th><th class="num">Imported</th><th class="num">Already there</th>
          <th class="num">Unusable</th><th></th></tr></thead>
        <tbody>${done.map(r => `<tr>
          <td>${esc(r.supplier_name)}</td>
          <td class="num">${fmt(r.inserted || 0)}</td>
          <td class="num">${fmt(r.already_present || 0)}</td>
          <td class="num">${fmt(r.skipped || 0)}</td>
          <td class="small ${r.error ? 'danger' : 'faint'}">${esc(r.error || 'ok')}</td>
        </tr>`).join('')}</tbody>
      </table></div></div>
      ${queued ? `<div class="card-head" style="border-top:1px solid var(--border);border-bottom:none">
        <button class="btn-primary" onclick="location.hash='#/pipeline'">Run pipeline</button></div>` : ''}
      </div>`;

    toast(`Imported ${total} hotels from ${done.length - failed} tab(s)`);
  },

  async analyseDb() {
    const payload = {
      supplier_name: el('dbSupplier').value.trim(),
      connection_url: el('dbUrl').value.trim(),
      query: el('dbQuery').value.trim(),
    };
    if (!payload.supplier_name || !payload.connection_url || !payload.query)
      return toast('Fill in all three fields', true);

    el('importReport').innerHTML = '<div class="spinner">Connecting…</div>';
    try {
      const d = await post(`${API}/import/database/analyse`, payload);
      this.state.dbPayload = payload;
      this.renderImportReport(d, 'db');
    } catch (e) { el('importReport').innerHTML = ''; toast(e.message, true); }
  },

  renderImportReport(d, source) {
    const fieldLabels = {
      supplier_hotel_id: 'Hotel ID', hotel_name: 'Hotel name', address: 'Address',
      city: 'City', state: 'State', country: 'Country', postal_code: 'Postal code',
      latitude: 'Latitude', longitude: 'Longitude', latlon: 'Coordinates (combined)',
      star_rating: 'Star rating', normalized_name: 'Normalised name', supplier_name: 'Supplier',
    };
    const issues = d.issues_in_sample;
    const anyIssue = Object.values(issues).some(v => v > 0);

    el('importReport').innerHTML = `
      ${d.ok ? '' : `<div class="alert alert-danger"><div class="alert-icon">!</div><div class="alert-body">
        <div class="alert-title">Cannot import — required columns not found</div>
        <div class="alert-text">Missing: ${d.missing_required.join(', ')}.
          The file must have a hotel ID, a hotel name and a country.</div></div></div>`}

      ${d.supplier_column_in_file?.length ? `<div class="alert alert-info"><div class="alert-icon">i</div>
        <div class="alert-body"><div class="alert-title">The file has its own supplier column</div>
        <div class="alert-text">It contains: ${d.supplier_column_in_file.map(esc).join(', ')}.
          These rows will be imported as <strong>${esc(d.supplier_name)}</strong> — the name you entered.</div>
        </div></div>` : ''}

      <div class="card"><div class="card-head"><h2>What we found</h2>
        <div class="spacer"></div><span class="muted small">${fmt(d.total_rows)} rows</span></div>
        <div class="card-body">
          <div class="row">
            <div class="col">
              <label>Columns matched</label>
              <table><tbody>${Object.entries(d.detected).map(([f, c]) => `<tr>
                <td>${fieldLabels[f] || f}</td><td class="faint">←</td><td class="mono">${esc(c)}</td></tr>`).join('')}
              </tbody></table>
              ${d.unmapped_headers?.length ? `<p class="small faint" style="margin-bottom:0">
                Ignored: ${d.unmapped_headers.map(esc).join(', ')}</p>` : ''}
            </div>
            <div class="col">
              <label>Data quality in the first ${d.sample_size} rows</label>
              <table><tbody>
                <tr><td>Missing coordinates</td><td class="num">${issues.no_coordinates}</td></tr>
                <tr><td>Missing city</td><td class="num">${issues.no_city}</td></tr>
                <tr><td>Missing postal code</td><td class="num">${issues.no_postal_code}</td></tr>
                <tr><td>Missing hotel name</td><td class="num">${issues.no_hotel_name}</td></tr>
              </tbody></table>
              ${anyIssue ? `<p class="small muted">Rows without a hotel name or country are skipped.
                Rows missing coordinates import but will be set aside during mapping.</p>` : ''}
            </div>
          </div>
        </div>
      </div>

      <div class="card"><div class="card-head"><h2>Sample of what will be imported</h2></div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Hotel name</th><th>City</th><th>Country</th><th>Postal</th><th class="num">Lat</th><th class="num">Lon</th></tr></thead>
          <tbody>${d.sample.map(r => `<tr>
            <td>${esc(r.hotel_name || '—')}</td><td>${esc(r.city || '—')}</td>
            <td>${esc(r.country || '—')}</td><td>${esc(r.postal_code || '—')}</td>
            <td class="num">${r.latitude ?? '—'}</td><td class="num">${r.longitude ?? '—'}</td>
          </tr>`).join('')}</tbody>
        </table></div></div>
        <div class="card-head" style="border-top:1px solid var(--border);border-bottom:none">
          <button class="btn-primary" onclick="App.commitImport('${source}')" ${d.ok ? '' : 'disabled'}>
            Import ${fmt(d.total_rows)} rows</button>
          <label style="display:flex;align-items:center;gap:6px;margin:0">
            <input type="checkbox" id="impQueue" checked> Queue them for mapping straight away</label>
        </div>
      </div>`;
  },

  async commitImport(source) {
    const enqueue = el('impQueue').checked;
    toast('Importing…');
    try {
      let result;
      if (source === 'file') {
        const fd = new FormData();
        fd.append('supplier_name', this.state.importSupplier);
        fd.append('token', this.state.importToken);
        fd.append('enqueue', enqueue);
        const r = await fetch(`${API}/import/file/commit`, { method: 'POST', body: fd });
        result = await r.json();
        if (!r.ok) throw new Error(result.detail || 'Import failed');
      } else {
        result = await post(`${API}/import/database/commit`, { ...this.state.dbPayload, enqueue });
      }
      el('importReport').innerHTML = `<div class="alert alert-ok"><div class="alert-icon">✓</div>
        <div class="alert-body"><div class="alert-title">Imported ${fmt(result.inserted)} hotels for ${esc(result.supplier_name)}</div>
        <div class="alert-text">${fmt(result.skipped)} row(s) skipped for missing a name or country.
          ${result.queued ? `${fmt(result.queued)} queued for mapping — go to Run Pipeline to process them.` : ''}</div></div>
        ${result.queued ? `<button class="btn-primary btn-sm" onclick="location.hash='#/pipeline'">Run pipeline</button>` : ''}</div>`;
      toast(`Imported ${result.inserted} hotels`);
    } catch (e) { toast(e.message, true); }
  },

  /* ── Pipeline ───────────────────────────────────────────────────────── */

  async render_pipeline() {
    const s = await get(`${API}/pipeline/status`);
    const done = s.completed + s.flagged + s.failed;

    el('view').innerHTML = `
      ${s.pending > 0 ? `<div class="alert alert-info"><div class="alert-icon">▶</div><div class="alert-body">
        <div class="alert-title">${fmt(s.pending)} hotel(s) waiting to be mapped</div>
        <div class="alert-text">Press Run to start. You can leave this page — it keeps going.</div></div>
        <button class="btn-primary btn-sm" onclick="App.runPipeline()">Run now</button></div>`
      : `<div class="alert alert-ok"><div class="alert-icon">✓</div><div class="alert-body">
        <div class="alert-title">Everything has been processed</div>
        <div class="alert-text">Import more data to run the pipeline again.</div></div></div>`}

      <div class="stat-grid">
        ${stat('Waiting', fmt(s.pending), 'not yet mapped', s.pending ? 'warn' : '')}
        ${stat('In progress', fmt(s.processing), 'being mapped now')}
        ${stat('Mapped', fmt(s.completed), 'finished', 'ok')}
        ${stat('Set aside', fmt(s.flagged), 'could not be mapped', s.flagged ? 'warn' : '')}
        ${stat('Failed', fmt(s.failed), 'errored', s.failed ? 'danger' : '')}
      </div>

      <div class="card"><div class="card-head"><h2>Progress</h2><div class="spacer"></div>
        <span class="muted small">${s.progress_pct}% of ${fmt(s.total)}</span></div>
        <div class="card-body">
          <div class="bar" style="height:14px">
            <span style="width:${(s.completed / (s.total || 1) * 100).toFixed(1)}%;background:var(--ok)"></span>
            <span style="width:${(s.flagged / (s.total || 1) * 100).toFixed(1)}%;background:var(--warn)"></span>
            <span style="width:${(s.failed / (s.total || 1) * 100).toFixed(1)}%;background:var(--danger)"></span>
          </div>
          <div class="toolbar" style="margin-top:14px">
            <button class="btn-primary" onclick="App.runPipeline()" ${s.pending ? '' : 'disabled'}>Run pipeline</button>
            <button class="btn-danger" onclick="App.resetPipeline()">Reset pipeline</button>
            ${s.failed ? `<button onclick="App.requeueFailed()">Retry ${fmt(s.failed)} failed</button>` : ''}
            ${exportBtn('supplier-hotels', 'Export all records')}
          </div>
        </div>
      </div>`;

    if (s.processing > 0 || (s.pending > 0 && this.state.polling)) {
      clearTimeout(this.state.pollTimer);
      this.state.pollTimer = setTimeout(() => { if (this.page === 'pipeline') this.refresh(); }, 5000);
    }
  },

  async runPipeline() {
    try {
      const r = await post(`${API}/pipeline/run`, { limit: 500 });
      if (!r.started) return toast(r.message);
      this.state.polling = true;
      toast(`Started — ${fmt(r.pending)} hotels to process`);
      setTimeout(() => this.refresh(), 1500);
    } catch (e) { toast(e.message, true); }
  },

  async requeueFailed() {
    const r = await post(`${API}/pipeline/requeue-failed`, {});
    toast(`${r.requeued} record(s) put back in the queue`);
    this.refresh();
  },

  async resetPipeline() {
    let p;
    try { p = await get(`${API}/mapping/reset/preview`); }
    catch (e) { return toast(e.message, true); }

    // States what will actually go and what will survive, from live counts.
    // "Are you sure?" tells a reviewer nothing they can weigh.
    showModal('Reset the pipeline?', `
      <p>Clears every mapping result so the same import can be re-run after a
         change to the matcher, the weights or the normalizer.</p>
      <table><tbody>
        <tr><td><strong>Deleted</strong></td><td class="num">${fmt(p.mappings)}</td><td class="muted">mappings</td></tr>
        <tr><td></td><td class="num">${fmt(p.masters)}</td><td class="muted">master hotels</td></tr>
        <tr><td></td><td class="num">${fmt(p.review_candidates)}</td><td class="muted">review candidates</td></tr>
        <tr><td></td><td class="num">${fmt(p.embeddings)}</td><td class="muted">embeddings</td></tr>
        <tr><td></td><td class="num">${fmt(p.discarded)}</td><td class="muted">discarded records</td></tr>
        <tr class="total-row"><td><strong>Kept</strong></td><td class="num">${fmt(p.supplier_records_kept)}</td><td class="muted">supplier records, re-queued</td></tr>
        <tr><td></td><td class="num">${fmt(p.public_ids_kept)}</td><td class="muted">public IDs (HBM-…) and their history</td></tr>
        <tr><td></td><td class="num">${fmt(p.anchors_kept)}</td><td class="muted">ID anchors, so the same hotels reclaim the same IDs</td></tr>
        <tr><td></td><td class="num">${fmt(p.split_decisions_kept)}</td><td class="muted">reviewer split decisions</td></tr>
      </tbody></table>
      <p class="small muted" style="margin-top:12px">Type <strong>RESET</strong> to confirm.</p>
      <input type="text" id="resetToken" placeholder="RESET" autocomplete="off" style="width:100%">
    `, [
      { label: 'Cancel', fn: () => closeModal() },
      { label: 'Reset pipeline', cls: 'btn-danger', fn: () => this.confirmReset() },
    ]);
  },

  async confirmReset() {
    const token = (el('resetToken')?.value || '').trim();
    if (token !== 'RESET') return toast('Type RESET to confirm', true);

    try {
      const r = await post(`${API}/mapping/reset`, { confirm: token });
      closeModal();
      toast(`Reset complete — ${fmt(r.pending_for_reprocessing)} hotels ready to re-process`);
      this.refresh();
    } catch (e) { toast(e.message, true); }
  },

  /* ── Exports ────────────────────────────────────────────────────────── */

  async render_exports() {
    const d = await get(`${API}/export`);
    const notes = {
      'master-hotels': 'One row per unique hotel, with its permanent ID',
      'mappings': 'Every supplier record and the hotel it was matched to, with the evidence',
      'discarded-records': 'Records left out of mapping, with the reason for each',
      'manual-review': 'Hotels waiting for a human decision',
      'supplier-hotels': 'Everything imported, exactly as supplied',
      'supplier-quality': 'Discard rate and missing fields per supplier',
      'integrity': 'Masters worth a second look',
      'reviewer-decisions': 'Splits your team has made — permanent',
      'supplier-id-conflicts': 'Supplier IDs reused for different hotels — send this to the supplier',
    };
    el('view').innerHTML = `
      <div class="card"><div class="card-head"><h2>Download data as Excel</h2></div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Dataset</th><th>What it contains</th><th></th></tr></thead>
          <tbody>${d.datasets.map(x => `<tr>
            <td><strong>${esc(x.title)}</strong></td>
            <td class="muted">${esc(notes[x.key] || '')}</td>
            <td>${exportBtn(x.key, 'Download')}</td>
          </tr>`).join('')}</tbody>
        </table></div></div>
        <div class="card-body" style="border-top:1px solid var(--border)">
          <p class="small muted" style="margin:0">Files open in Excel with filters already switched on.
            Large exports may take a few seconds to prepare.</p>
        </div>
      </div>`;
  },

  /* ── Supplier quality ───────────────────────────────────────────────── */

  async render_suppliers() {
    const [d, disc] = await Promise.all([get(`${API}/dashboard`), get(`${API}/discarded/by-supplier`)]);
    const byName = Object.fromEntries(disc.suppliers.map(s => [s.supplier_name, s]));

    el('view').innerHTML = `
      <div class="card"><div class="card-head"><h2>Data quality by supplier</h2>
        <div class="spacer"></div>${exportBtn('supplier-quality')}${exportBtn('supplier-id-conflicts','Export ID conflicts')}</div>
        <div class="card-body flush"><div class="table-wrap"><table>
          <thead><tr><th>Supplier</th><th class="num">Records supplied</th><th class="num">Mapped</th>
            <th class="num">Discarded</th><th class="num">Discard rate</th><th>Quality</th></tr></thead>
          <tbody>${d.suppliers.map(s => {
            const rate = s.supplied ? (s.discarded / s.supplied * 100) : 0;
            const cls = rate > 5 ? 'high' : rate > 1 ? 'medium' : 'low';
            const word = rate > 5 ? 'Needs attention' : rate > 1 ? 'Minor issues' : 'Good';
            return `<tr>
              <td><strong>${esc(s.supplier_name)}</strong></td>
              <td class="num">${fmt(s.supplied)}</td>
              <td class="num">${fmt(s.mapped)}</td>
              <td class="num">${fmt(s.discarded)}</td>
              <td class="num">${rate.toFixed(1)}%</td>
              <td><span class="chip chip-${cls}">${word}</span></td>
            </tr>`;
          }).join('')}</tbody>
        </table></div></div>
        <div class="card-body" style="border-top:1px solid var(--border)">
          <p class="small muted" style="margin:0">A high discard rate means the supplier is sending records we cannot map safely —
            missing coordinates, reused hotel IDs, or incomplete names. This is a supplier conversation, not a mapping problem.</p>
        </div>
      </div>`;
  },
};

/* ── helpers ──────────────────────────────────────────────────────────── */

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmt(n) { return Number(n ?? 0).toLocaleString(); }

function tierDesc(t) {
  return { TIER1: 'Near-identical name, under 100m', TIER2: 'Strong name match, under 200m',
           TIER3: 'Good name match, under 500m', TIER4: 'Weaker name or further apart' }[t] || '';
}

// A totals row, so the per-supplier columns can be reconciled against the
// headline figures above without adding them up by hand.
function supplierTotals(rows) {
  if (!rows || rows.length < 2) return '';

  const sum = k => rows.reduce((a, r) => a + Number(r[k] ?? 0), 0);
  const supplied = sum('supplied');
  const mapped = sum('mapped');
  const pct = supplied ? (mapped / supplied * 100) : 0;

  return `<tr class="total-row">
    <td><strong>All suppliers</strong></td>
    <td class="num"><strong>${fmt(supplied)}</strong></td>
    <td class="num"><strong>${fmt(mapped)}</strong></td>
    <td><span class="small"><strong>${pct.toFixed(1)}%</strong></span></td>
    <td class="num"><strong>${fmt(sum('auto_mapped'))}</strong></td>
    <td class="num"><strong>${fmt(sum('new_master'))}</strong></td>
    <td class="num"><strong>${fmt(sum('manual_mapped'))}</strong></td>
    <td class="num"><strong>${fmt(sum('manual_new_master'))}</strong></td>
    <td class="num"><strong>${fmt(sum('discarded'))}</strong></td>
  </tr>`;
}


// The queue holds two kinds of escalation and they carry different evidence:
// an exact-name conflict has a rule score and never an AI score, a semantic
// duplicate the reverse. Two half-empty columns invited the reading that the AI
// had failed, so the kind is named and the one score it does have is shown.
function reviewKind(type) {
  const kinds = {
    EXACT_NAME_GEO_CONFLICT:  'exact name',
    CITY_STRIPPED_NAME_MATCH: 'name minus city',
    AI_SEMANTIC_DUPLICATE:    'AI similarity',
  };
  return esc(kinds[type] || type || 'rule score');
}


// Star looks like a usable filter and mostly is not: `star_rating >= n`
// excludes NULLs, and only a fifth of supplier records carry a rating. Say so
// on the control itself, the way the empty chain and type fields already do.
function starCoverageNote(facets) {
  const c = facets?.star_coverage;
  if (!c || !c.total) return '';
  if (c.pct >= 90) return '';
  return `<span class="muted" style="font-weight:400">— only ${c.pct}% of records have one</span>`;
}

function exportBtn(dataset, label) {
  return `<button class="btn-sm" onclick="App.download('${dataset}')">${label || 'Export to Excel'}</button>`;
}


// The only figures in this product that measure correctness rather than
// self-consistency, and until now they existed solely in an API response.
// Published and overall are shown together on purpose: a consumer only ever
// sees the published set, but hiding the difference would make the gate look
// like an accuracy claim rather than a decision about what to assert.

// The demo's headline. A false merge in the published set is the one failure
// the whole design prevents, so its status is stated first, in words, in a
// colour that reads across a room — green when nothing published is wrong, red
// the instant something is. Unmeasured (no reference loaded) is neither: it says
// so rather than showing a green all-clear it has not earned.
function gateBanner(gate) {
  if (!gate) return '';

  if (!gate.measured) {
    return `<div class="gate gate-unknown">
      <div class="gate-mark">–</div>
      <div class="gate-body">
        <div class="gate-title">Accuracy not measured</div>
        <div class="gate-sub">${esc(gate.reason || 'No reference mapping loaded.')}</div>
      </div></div>`;
  }

  if (gate.passing) {
    return `<div class="gate gate-pass">
      <div class="gate-mark">✓</div>
      <div class="gate-body">
        <div class="gate-title">Safe to publish — 0 wrong merges</div>
        <div class="gate-sub">Verified across ${fmt(gate.hotels_measured)} hotels and
          ${fmt(gate.masters_published)} published masters, against the reference mapping.</div>
      </div></div>`;
  }

  return `<div class="gate gate-fail">
    <div class="gate-mark">!</div>
    <div class="gate-body">
      <div class="gate-title">${fmt(gate.published_false_merges)} wrong merge(s) in published data</div>
      <div class="gate-sub">A regression reached what consumers see. Do not publish this run —
        check the false-merges list before releasing.</div>
    </div></div>`;
}

function accuracyCard(acc) {
  if (!acc || !acc.available) return '';

  const p = acc.published_only || {};
  const a = acc.precision || {};
  const r = acc.recall || {};
  const clean = p.false_merges === 0;

  return `
    <div class="card">
      <div class="card-head">
        <h2>Accuracy against the reference mapping</h2>
        <div class="spacer"></div>
        <span class="muted small">${fmt(acc.coverage?.reference_hotels)} hotels ·
          ${fmt(acc.coverage?.reference_pairs)} provider records</span>
      </div>
      <div class="card-body">
        <div class="stat-grid" style="margin-bottom:0">
          ${stat('Wrong merges, published', fmt(p.false_merges ?? '—'),
                 clean ? 'nothing published is wrong' : 'needs attention',
                 clean ? 'ok' : 'danger')}
          ${stat('Hotels kept whole', `${r.pct_intact ?? '—'}%`,
                 `${fmt(r.intact)} of ${fmt(r.hotels_evaluated)} · ${fmt(r.split)} split apart`,
                 'warn')}
          ${stat('Published', fmt(p.published), `${fmt(p.held)} held for review`)}
          ${stat('Wrong merges, all', fmt(a.false_merges ?? '—'),
                 'including held mappings', a.false_merges ? 'warn' : 'ok')}
        </div>
        <p class="small muted" style="margin:12px 0 0">
          Measured against an existing production mapping, not sampled. Zero errors across
          ${fmt(r.hotels_evaluated)} hotels bounds the true rate below about 0.17% — strong evidence,
          not a guarantee. Only TIER1 and TIER2 reach a consumer; the rest wait for a person.
        </p>
      </div>
    </div>`;
}

function stat(label, value, sub, cls = '') {
  return `<div class="stat ${cls}"><div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div><div class="stat-sub">${sub || ''}</div></div>`;
}

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Request failed (${r.status})`);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Request failed (${r.status})`);
  return r.json();
}

function showModal(title, body, buttons) {
  el('modalHost').innerHTML = `<div class="modal-backdrop" onclick="if(event.target===this)closeModal()">
    <div class="modal"><div class="modal-head">${esc(title)}</div>
      <div class="modal-body">${body}</div>
      <div class="modal-foot">${buttons.map((b, i) => `<button class="${b.cls || ''}" data-i="${i}">${esc(b.label)}</button>`).join('')}</div>
    </div></div>`;
  el('modalHost').querySelectorAll('.modal-foot button').forEach((btn, i) => btn.onclick = buttons[i].fn);
}
function closeModal() { el('modalHost').innerHTML = ''; }

let toastTimer;
function toast(msg, isErr) {
  el('toastHost').innerHTML = `<div class="toast ${isErr ? 'err' : ''}">${esc(msg)}</div>`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el('toastHost').innerHTML = '', 4200);
}

App.init();
