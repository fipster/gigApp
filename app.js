
    // ---- DATA ----
    // One entry per confirmed show. Add more bands by adding more entries —
    // the band dropdown and city filter both build themselves from this list.

    let shows = [];
    let countries = {};
    let cityCoordinates = {};
    let countryCoordinates = {};
    let bandPriorities = new Map();
    let uniqueBands = [];
    let uniqueSources = [];
    let WINDOW_TO;

    async function loadShows() {
      const [showsResponse, countriesResponse, coordsResponse, countryCoordsResponse] = await Promise.all([
        fetch("shows.json"),
        fetch("countries.json"),
        fetch("city_coordinates.json"),
        fetch("country_coordinates.json"),
      ]);
      shows = await showsResponse.json();
      countries = await countriesResponse.json();
      cityCoordinates = await coordsResponse.json();
      countryCoordinates = await countryCoordsResponse.json();

      shows.forEach(s => bandPriorities.set(s.band, s.priority));
      // computed once here rather than re-derived from `shows` on every
      // render()/panel-rebuild/reset -- `shows` itself never changes after
      // this point, so these never go stale
      uniqueBands = [...new Set(shows.map(s => s.band))].sort();
      uniqueSources = [...new Set(shows.map(s => s.source))].sort();

      WINDOW_TO = shows.reduce((max, s) => s.date > max ? s.date : max, WINDOW_FROM);

      const dateFromEl = document.getElementById('dateFrom');
      const dateToEl = document.getElementById('dateTo');
      dateFromEl.min = WINDOW_FROM;
      dateFromEl.max = WINDOW_TO;
      dateToEl.min = WINDOW_FROM;
      dateToEl.max = WINDOW_TO;
      dateFromEl.value = WINDOW_FROM;
      dateToEl.value = WINDOW_TO;

      bandFacet.rebuild();
      sourceFacet.rebuild();
      cityFacet.rebuild();
      render();
    }

    loadShows();

    function todayISO() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    function toISO(d) {
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    function addDays(dateStr, days) {
      const d = new Date(dateStr + "T00:00:00");
      d.setDate(d.getDate() + days);
      return toISO(d);
    }

    function mondayStrictlyAfter(dateStr) {
      const d = new Date(dateStr + "T00:00:00");
      const diff = (8 - d.getDay()) % 7 || 7;
      d.setDate(d.getDate() + diff);
      return toISO(d);
    }

    function saturdayOnOrAfter(dateStr) {
      const d = new Date(dateStr + "T00:00:00");
      const diff = (6 - d.getDay() + 7) % 7;
      d.setDate(d.getDate() + diff);
      return toISO(d);
    }

    // mirrors weekNext's mondayStrictlyAfter jump in reverse, so the same
    // sequence of weeks is retraced going backward
    function candidatePrevWeekStart() {
      const firstMonday = mondayStrictlyAfter(addDays(weekAnchor, 6));
      return (weekStart === firstMonday) ? weekAnchor : addDays(weekStart, -7);
    }

    function isWeekEntirelyPast(candidateStart) {
      return addDays(candidateStart, 6) < todayISO();
    }

    const WINDOW_FROM = todayISO();

    const monthNames = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    const dowNames = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

    const SCHOOL_HOLIDAYS = {
      vaheaeg1: { label: "vaheaeg I", from: "2026-10-23", to: "2026-10-31" },
      vaheaeg2: { label: "vaheaeg II", from: "2026-12-23", to: "2027-01-09" },
      vaheaeg3: { label: "vaheaeg III", from: "2027-02-19", to: "2027-02-27" },
      vaheaeg4: { label: "vaheaeg IV", from: "2027-04-09", to: "2027-04-17" },
      vaheaeg5: { label: "vaheaeg V", from: "2027-06-11", to: "2027-08-30" },
    };

    function countryFlag(code) { return countries[code]?.flag || ""; }
    function countryName(code) { return countries[code]?.name || code; }

    let bandMode = "include";
    let bandSelection = new Set();
    let sourceMode = "include";
    let sourceSelection = new Set();
    let activeOrigins = new Set();
    let activePriorities = new Set();
    let excludeFest = false;
    let cityMode = "include";
    let citySelection = new Set();
    let datePreset = "all";
    let dateFrom = null;
    let dateTo = null;
    let sortBy = "date";
    let viewMode = "list";
    let weekStart = todayISO();
    let weekAnchor = weekStart; // earliest week start reachable via "prev week" -- there's no show data before it
    let leafletMap = null;
    let leafletMarkers = [];

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function escapeAttr(str) {
      return escapeHtml(str).replace(/"/g, '&quot;');
    }

    // supports comma-separated search terms: "radiohead, metallica" matches
    // either, so adding a new term accumulates results instead of replacing them
    function matchesSearchTerms(text, rawInput) {
      const terms = rawInput.split(",").map(t => t.trim().toLowerCase()).filter(Boolean);
      if (terms.length === 0) return true;
      const lower = text.toLowerCase();
      return terms.some(term => lower.includes(term));
    }

    function formatWindowLabel(from, to) {
      const f = new Date(from + "T00:00:00");
      const t = new Date(to + "T00:00:00");
      return `${monthNames[f.getMonth()]} ${f.getFullYear()} – ${monthNames[t.getMonth()]} ${t.getFullYear()}`;
    }

    function bandPriorityClass(bandName) {
      const priority = bandPriorities.get(bandName);
      if (priority === "1") return "is-priority";
      if (priority === "2") return "is-priority-2";
      return "";
    }

    function cityKey(s) { return s.country + "|" + s.city; }

    // ---- SHARED FILTER FACET (band / source / city panels) ----
    // A "facet" is a checkbox-list filter panel: search box, include/exclude
    // mode toggle, select-all/none, and (when its <details> is opened) hiding
    // rows with no matches under the OTHER currently-active filters. Band and
    // Source are flat lists; City groups cities under a country row whose own
    // checkbox reflects its children's state -- buildRows() is the only piece
    // that differs between them. Everything else here is structure-agnostic:
    // it only cares about elements carrying a `data-key` attribute (each
    // leaf checkbox) and, optionally, an ancestor `.country-group`.
    //
    // Search only narrows which rows are *visible* in the dropdown -- it
    // never touches `selection` or a checkbox's `checked` state. (An earlier
    // version did mutate them on every keystroke, so clearing the search box
    // silently re-selected everything and discarded any manual deselection --
    // this is the fix for that.) Because of that, typing in the search box
    // no longer needs to re-render the main shows list at all: nothing the
    // list depends on (`selection`) has changed, only what's visible inside
    // the not-yet-committed dropdown.
    function createFilterFacet({ panelId, searchInputId, modeRowSelector, modeDataAttr, selectAllId, selectNoneId, selection, setMode, buildRows, allKeys }) {
      const panel = document.getElementById(panelId);
      const searchInput = document.getElementById(searchInputId);

      // the checkbox itself carries data-key (set in wireRow below) -- use
      // .closest('.city-row') on any of these when the row/label wrapper
      // (for visibility/text) is what's actually needed
      function leafCheckboxes() {
        return [...panel.querySelectorAll('[data-key]')];
      }

      // keeps a city-group's own checkbox in sync with its children -- a
      // no-op for band/source, which have no `.country-group` ancestor
      function syncGroupCheckbox(row) {
        const group = row.closest('.country-group');
        if (!group) return;
        const groupCb = group.querySelector('.country-row input[type=checkbox]');
        const children = [...group.querySelectorAll('[data-key]')];
        groupCb.checked = children.length > 0 && children.every(cb => cb.checked);
      }

      function wireRow(row, key) {
        const cb = row.querySelector('input[type=checkbox]');
        cb.checked = true;
        cb.dataset.key = key;
        cb.addEventListener('change', () => {
          if (cb.checked) selection.add(key); else selection.delete(key);
          syncGroupCheckbox(row);
          render();
        });
      }

      function rebuild() {
        selection.clear();
        allKeys().forEach(k => selection.add(k));
        panel.innerHTML = "";
        buildRows(panel, wireRow, selection);
      }

      searchInput.addEventListener('input', (e) => {
        const rawInput = e.target.value;
        leafCheckboxes().forEach(cb => {
          const row = cb.closest('.city-row');
          row.style.display = matchesSearchTerms(row.textContent, rawInput) ? "" : "none";
        });
        panel.querySelectorAll('.country-group').forEach(group => {
          const anyVisible = [...group.querySelectorAll('[data-key]')].some(cb => cb.closest('.city-row').style.display !== "none");
          group.style.display = anyVisible ? "" : "none";
        });
      });

      document.querySelectorAll(modeRowSelector).forEach(chip => {
        chip.addEventListener('click', () => {
          document.querySelectorAll(modeRowSelector).forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          setMode(chip.dataset[modeDataAttr]);
          render();
        });
      });

      document.getElementById(selectAllId).addEventListener('click', () => {
        leafCheckboxes().forEach(cb => { cb.checked = true; });
        panel.querySelectorAll('.country-group input[type=checkbox]').forEach(cb => cb.checked = true);
        selection.clear();
        allKeys().forEach(k => selection.add(k));
        render();
      });
      document.getElementById(selectNoneId).addEventListener('click', () => {
        leafCheckboxes().forEach(cb => { cb.checked = false; });
        panel.querySelectorAll('.country-group input[type=checkbox]').forEach(cb => cb.checked = false);
        selection.clear();
        render();
      });

      // hides rows that have no shows under the OTHER active filters (dates,
      // other facets, etc.) -- run when the panel is opened, so it reflects
      // whatever's currently filtered elsewhere without touching selection
      function refreshAvailability(available) {
        panel.querySelectorAll('.country-group').forEach(group => {
          let anyVisible = false;
          group.querySelectorAll('[data-key]').forEach(cb => {
            const row = cb.closest('.city-row');
            const visible = available.has(cb.dataset.key);
            row.style.display = visible ? "" : "none";
            if (visible) anyVisible = true;
          });
          group.style.display = anyVisible ? "" : "none";
        });
        if (!panel.querySelector('.country-group')) {
          leafCheckboxes().forEach(cb => {
            cb.closest('.city-row').style.display = available.has(cb.dataset.key) ? "" : "none";
          });
        }
      }

      function reset() {
        setMode("include");
        document.querySelectorAll(modeRowSelector).forEach(c => c.classList.remove('active'));
        // modeDataAttr (e.g. "bandmode") is a single lowercase word for all
        // three facets, so it maps directly to its data-* attribute name
        // with no camelCase conversion needed
        document.querySelector(`${modeRowSelector}[data-${modeDataAttr}="include"]`)?.classList.add('active');
        searchInput.value = "";
        panel.querySelectorAll('.country-group').forEach(g => g.style.display = "");
        leafCheckboxes().forEach(cb => { cb.closest('.city-row').style.display = ""; cb.checked = true; });
        panel.querySelectorAll('.country-group input[type=checkbox]').forEach(cb => cb.checked = true);
        selection.clear();
        allKeys().forEach(k => selection.add(k));
      }

      return { rebuild, refreshAvailability, reset };
    }

    function buildFlatRows(getLabel) {
      return (panel, wireRow, selection) => {
        const keys = [...selection]; // rebuild() already seeded selection with allKeys()
        keys.sort().forEach(key => {
          const row = document.createElement('label');
          row.className = "city-row";
          row.style.paddingLeft = "0";
          const cb = document.createElement('input');
          cb.type = "checkbox";
          row.appendChild(cb);
          row.appendChild(document.createTextNode(getLabel ? getLabel(key) : key));
          panel.appendChild(row);
          wireRow(row, key);
        });
      };
    }

    function buildCityRows(panel, wireRow) {
      const byCountry = {};
      const order = [];
      shows.forEach(s => {
        if (!byCountry[s.country]) { byCountry[s.country] = []; order.push(s.country); }
        if (!byCountry[s.country].some(c => c.city === s.city)) {
          byCountry[s.country].push({ city: s.city });
        }
      });

      order.sort((a, b) => countryName(a).localeCompare(countryName(b)));
      Object.values(byCountry).forEach(cities => cities.sort((a, b) => a.city.localeCompare(b.city)));

      order.forEach(country => {
        const cities = byCountry[country];
        const group = document.createElement('div');
        group.className = "country-group";

        const countryRow = document.createElement('label');
        countryRow.className = "country-row";
        const countryCb = document.createElement('input');
        countryCb.type = "checkbox";
        countryCb.checked = true;
        countryRow.appendChild(countryCb);
        countryRow.appendChild(document.createTextNode(countryFlag(country) + " " + countryName(country)));
        group.appendChild(countryRow);

        const cityCbs = [];
        cities.forEach(c => {
          const key = country + "|" + c.city;
          const cityRow = document.createElement('label');
          cityRow.className = "city-row";
          const cityCb = document.createElement('input');
          cityCb.type = "checkbox";
          cityRow.appendChild(cityCb);
          cityRow.appendChild(document.createTextNode(c.city));
          group.appendChild(cityRow);
          cityCbs.push(cityCb);
          wireRow(cityRow, key);
        });

        countryCb.addEventListener('change', () => {
          cityCbs.forEach(cb => {
            cb.checked = countryCb.checked;
            const key = cb.dataset.key;
            if (cb.checked) citySelection.add(key); else citySelection.delete(key);
          });
          render();
        });

        panel.appendChild(group);
      });
    }

    const bandFacet = createFilterFacet({
      panelId: 'bandCheckboxList',
      searchInputId: 'bandSearchInput',
      modeRowSelector: '#bandFilterDetails .mode-row .chip',
      modeDataAttr: 'bandmode',
      selectAllId: 'selectAllBands',
      selectNoneId: 'selectNoneBands',
      selection: bandSelection,
      setMode: (v) => { bandMode = v; },
      buildRows: buildFlatRows(),
      allKeys: () => uniqueBands,
    });

    const sourceFacet = createFilterFacet({
      panelId: 'sourceCheckboxList',
      searchInputId: 'sourceSearchInput',
      modeRowSelector: '#sourceFilterDetails .mode-row .chip',
      modeDataAttr: 'sourcemode',
      selectAllId: 'selectAllSources',
      selectNoneId: 'selectNoneSources',
      selection: sourceSelection,
      setMode: (v) => { sourceMode = v; },
      buildRows: buildFlatRows(),
      allKeys: () => uniqueSources,
    });

    const cityFacet = createFilterFacet({
      panelId: 'cityCheckboxList',
      searchInputId: 'citySearchInput',
      modeRowSelector: '#cityFilterDetails .mode-row .chip',
      modeDataAttr: 'mode',
      selectAllId: 'selectAllCities',
      selectNoneId: 'selectNoneCities',
      selection: citySelection,
      setMode: (v) => { cityMode = v; },
      buildRows: buildCityRows,
      allKeys: () => [...new Set(shows.map(cityKey))],
    });

    function hasDirectForSelection(show) {
      if (activeOrigins.size === 0) return true;
      return (activeOrigins.has("TLL") && show.flightTLL.direct) ||
             (activeOrigins.has("RIX") && show.flightRIX.direct);
    }

    function formatDuration(minutes) {
      if (!minutes && minutes !== 0) return "";
      const h = Math.floor(minutes / 60);
      const m = minutes % 60;
      return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function youtubeMusicUrl(band) {
      return "https://music.youtube.com/search?q=" + encodeURIComponent(band);
    }

    function googleFlightsUrl(city, date) {
      const q = `Flights from TLL or RIX to ${city} on ${date}`;
      return `https://www.google.com/travel/flights?q=${encodeURIComponent(q)}`;
    }

    function flightPillHtml(originLabel, f, date) {
      const carrierNames = (f.carriers || []).map(c => c.name).join(", ");
      const title = f.direct && carrierNames ? ` title="${escapeAttr(carrierNames)}"` : "";
      const href = googleFlightsUrl(f.hub_city, date);
      return `<a class="flight-pill${f.direct ? "" : " none"}" href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer"${title}><span class="airport-badge">${originLabel}</span></a>`;
    }

    function carrierLogosHtml(carriers) {
      if (!carriers) return "";
      return carriers.map(c =>
        `<img class="carrier-logo" src="https://images.kiwi.com/airlines/64/${c.iata}.png" alt="${escapeAttr(c.name)}" title="${escapeAttr(c.name)}" loading="lazy" onerror="this.remove()">`
      ).join("");
    }

    function flightSummaryHtml(s) {
      if (!s.flightTLL.direct && !s.flightRIX.direct) return "";
      const duration = formatDuration(s.flightTLL.duration_minutes);
      if (!duration) return "";
      const seasonal = s.flightTLL.seasonal ? " (seasonal)" : "";
      return `<span class="flight-duration">${duration}${seasonal}</span>`;
    }

    // exclude lets a filter panel see what it WOULD show if its own facet
    // weren't applied yet -- e.g. the city panel wants "cities reachable
    // under the current date/band/source filters", not "cities reachable
    // under the current city filter", which would just echo its own selection
    function getFilteredList(exclude) {
      let list = shows.slice();

      if (exclude !== "band") {
        if (bandMode === "include") {
          list = list.filter(s => bandSelection.has(s.band));
        } else {
          list = list.filter(s => !bandSelection.has(s.band));
        }
      }
      if (excludeFest) list = list.filter(s => !s.fest);
      if (exclude !== "date") {
        if (dateFrom) list = list.filter(s => s.date >= dateFrom);
        if (dateTo) list = list.filter(s => s.date <= dateTo);
      }
      if (exclude !== "source") {
        if (sourceMode === "include") {
          list = list.filter(s => sourceSelection.has(s.source));
        } else {
          list = list.filter(s => !sourceSelection.has(s.source));
        }
      }
      if (exclude !== "city") {
        if (cityMode === "include") {
          list = list.filter(s => citySelection.has(cityKey(s)));
        } else {
          list = list.filter(s => !citySelection.has(cityKey(s)));
        }
      }
      if (activeOrigins.size > 0) list = list.filter(s => hasDirectForSelection(s));
      if (activePriorities.size > 0) list = list.filter(s => activePriorities.has(s.priority));
      return list;
    }

    // group shows sharing the same date + venue (e.g. festival lineups) into one row
    function groupShows(list) {
      const groups = [];
      const groupByKey = new Map();
      list.forEach(s => {
        const key = `${s.date}|${s.venue}|${s.city}|${s.country}`;
        if (!groupByKey.has(key)) {
          const group = { ...s, bands: [s.band] };
          groupByKey.set(key, group);
          groups.push(group);
        } else {
          groupByKey.get(key).bands.push(s.band);
        }
      });
      groups.forEach(g => g.bands.sort());
      return groups;
    }

    function render() {
      const list = getFilteredList();

      document.getElementById('totalCount').textContent = shows.length + " confirmed";
      document.getElementById('windowLabel').textContent = formatWindowLabel(WINDOW_FROM, WINDOW_TO);

      const activeBandCount = new Set(list.map(s => s.band)).size;
      document.getElementById('bandFilterCount').textContent = `${activeBandCount}/${uniqueBands.length} bands`;

      const activeSourceCount = new Set(list.map(s => s.source)).size;
      document.getElementById('sourceFilterCount').textContent = `${activeSourceCount}/${uniqueSources.length} sources`;

      document.getElementById('cityFilterCount').textContent = `${list.length}/${shows.length} shows`;

      if (viewMode === "list") renderListView(list);
      // the weekly view isn't bound to the date-range filter -- it just
      // starts positioned at that range, but navigating (prev/next week)
      // should keep showing whatever's really scheduled that week rather
      // than going blank once you scroll outside the selected range
      else if (viewMode === "weekly") renderWeeklyView(getFilteredList("date"));
      else if (viewMode === "map") renderMapView(list);
    }

    function renderListView(list) {
      const groups = groupShows(list);

      if (sortBy === "bandCount") {
        groups.sort((a, b) => b.bands.length - a.bands.length || a.date.localeCompare(b.date));
      } else {
        groups.sort((a, b) => a.date.localeCompare(b.date));
      }

      const root = document.getElementById('route');
      root.innerHTML = "";

      if (list.length === 0) {
        root.innerHTML = '<div class="empty">NO SHOWS MATCH THESE FILTERS</div>';
        return;
      }

      let lastMonth = null;
      groups.forEach(s => {
        const d = new Date(s.date + "T00:00:00");
        const monthKey = monthNames[d.getMonth()] + " " + d.getFullYear();

        if (sortBy === "date" && monthKey !== lastMonth) {
          const div = document.createElement('div');
          div.className = "month-divider";
          div.textContent = monthKey;
          root.appendChild(div);
          lastMonth = monthKey;
        }

        const stub = document.createElement('div');
        stub.className = "stub";

        const tllCarrier = s.flightTLL.direct ? s.flightTLL.carriers : null;
        const rixCarrier = s.flightRIX.direct ? s.flightRIX.carriers : null;
        const carrierKey = (carriers) => (carriers || []).map(c => c.iata).sort().join(",");
        const sameCarrier = tllCarrier && carrierKey(tllCarrier) === carrierKey(rixCarrier);

        let flightsHtml = "";
        flightsHtml += flightPillHtml("TLL", s.flightTLL, s.date);
        if (!sameCarrier && tllCarrier) flightsHtml += carrierLogosHtml(tllCarrier);
        flightsHtml += flightPillHtml("RIX", s.flightRIX, s.date);
        if (sameCarrier) flightsHtml += carrierLogosHtml(tllCarrier);
        else if (rixCarrier) flightsHtml += carrierLogosHtml(rixCarrier);
        flightsHtml += flightSummaryHtml(s);

        const bandTagsHtml = s.bands.map(band =>
          `<a class="band-tag ${bandPriorityClass(band)}" href="${escapeAttr(youtubeMusicUrl(band))}" target="_blank" rel="noopener noreferrer">${escapeHtml(band)}</a>`
        ).join('');

        stub.innerHTML = `
      <div class="stub-dot"></div>
      <div class="date-block">
        <div class="dow">${dowNames[d.getDay()]}</div>
        <div class="dnum">${d.getDate()}</div>
        <div class="mon">${monthNames[d.getMonth()]} '${String(d.getFullYear()).slice(2)}</div>
      </div>
      <div class="stub-body">
        <div class="stub-top">
          <div class="band-tags">${bandTagsHtml}</div>
          ${s.fest ? `<span class="fest-tag">${escapeHtml(s.fest)}</span>` : ''}
        </div>
        <div class="city">${s.url ? `<a href="${escapeAttr(s.url)}" target="_blank" rel="noopener noreferrer">${countryFlag(s.country)} ${escapeHtml(s.city)}</a>` : `${countryFlag(s.country)} ${escapeHtml(s.city)}`}</div>
        <div class="venue">${s.url ? `<a href="${escapeAttr(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.venue)}</a>` : escapeHtml(s.venue)}</div>
        <div class="flights">${flightsHtml}</div>
        ${s.note ? `<div class="estimate-note">${escapeHtml(s.note)}</div>` : ''}
      </div>
    `;
        root.appendChild(stub);
      });
    }

    function formatWeekLabel(start) {
      const from = new Date(start + "T00:00:00");
      const to = new Date(addDays(start, 6) + "T00:00:00");
      const sameYear = from.getFullYear() === to.getFullYear();
      const fromStr = sameYear
        ? `${from.getDate()} ${monthNames[from.getMonth()]}`
        : `${from.getDate()} ${monthNames[from.getMonth()]} ${from.getFullYear()}`;
      const toStr = `${to.getDate()} ${monthNames[to.getMonth()]} ${to.getFullYear()}`;
      return `${fromStr} – ${toStr}`;
    }

    // toggles a "+N more" popover open/closed via click or Enter/Space, in
    // addition to the plain CSS :hover reveal -- so it's reachable on
    // touch devices and via keyboard, not just a mouse hover
    function toggleMoreBands(wrap, forceClose) {
      const isOpen = wrap.classList.contains('is-open');
      document.querySelectorAll('.w-more-wrap.is-open').forEach(w => { if (w !== wrap) w.classList.remove('is-open'); });
      wrap.classList.toggle('is-open', forceClose ? false : !isOpen);
    }

    function buildWeekShowEntry(s, showFlag) {
      const otherBands = s.bands.slice(1);
      let bandSuffix = '';
      if (otherBands.length > 0) {
        const bandsHtml = otherBands.map(band =>
          `<a class="w-band" href="${escapeAttr(youtubeMusicUrl(band))}" target="_blank" rel="noopener noreferrer">${escapeHtml(band)}</a>`
        ).join('');
        bandSuffix = ` <span class="w-more-wrap"><span class="w-more" tabindex="0" role="button" aria-label="${otherBands.length} more band(s)">+${otherBands.length}</span><div class="w-more-bands">${bandsHtml}</div></span>`;
      }
      const entry = document.createElement('div');
      entry.className = "week-show-entry"
        + (s.fest === "FESTIVAL" ? " is-festival" : "")
        + (s.priority === "1" ? " is-priority" : "")
        + (s.priority === "2" ? " is-priority-2" : "");
      const bandLink = `<div class="w-band-row"><a class="w-band" href="${escapeAttr(youtubeMusicUrl(s.bands[0]))}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.bands[0])}</a>${bandSuffix}</div>`;
      const cityText = (showFlag ? countryFlag(s.country) + " " : "") + s.city;
      const cityHtml = s.url
        ? `<a class="w-city" href="${escapeAttr(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(cityText)}</a>`
        : `<span class="w-city">${escapeHtml(cityText)}</span>`;
      entry.innerHTML = bandLink + cityHtml;

      const moreTrigger = entry.querySelector('.w-more');
      if (moreTrigger) {
        moreTrigger.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          toggleMoreBands(moreTrigger.closest('.w-more-wrap'));
        });
        moreTrigger.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleMoreBands(moreTrigger.closest('.w-more-wrap'));
          }
        });
      }
      return entry;
    }

    function renderWeekCellRow(grid, weekDates, today, groupsByDate, showFlag) {
      weekDates.forEach(dateStr => {
        const cell = document.createElement('div');
        cell.className = "week-cell" + (dateStr === today ? " is-today" : "");
        (groupsByDate.get(dateStr) || []).forEach(s => cell.appendChild(buildWeekShowEntry(s, showFlag)));
        grid.appendChild(cell);
      });
    }

    function groupByDate(groups) {
      const byDate = new Map();
      groups.forEach(g => {
        if (!byDate.has(g.date)) byDate.set(g.date, []);
        byDate.get(g.date).push(g);
      });
      return byDate;
    }

    function renderWeeklyView(list) {
      document.getElementById('weekLabel').textContent = formatWeekLabel(weekStart);
      document.getElementById('weekPrev').disabled = isWeekEntirelyPast(candidatePrevWeekStart());

      const weekDates = [];
      for (let i = 0; i < 7; i++) weekDates.push(addDays(weekStart, i));
      const weekDateSet = new Set(weekDates);

      const groups = groupShows(list).filter(g => weekDateSet.has(g.date));

      // each country gets its own header row (spanning all 7 day columns)
      // followed by a row of day-cells -- most shows this week on top;
      // a country only earns its own section if it has 2+ DIFFERENT artists
      // playing there AND those shows land on 2+ DIFFERENT days this week (a
      // dedicated trip is only worth it if there's a choice of shows spread
      // out over the week) -- one artist playing multiple nights/cities, or
      // several artists all on the same single day, are pooled into the
      // shared "Others" row instead
      const byCountry = new Map();
      groups.forEach(g => {
        if (!byCountry.has(g.country)) byCountry.set(g.country, []);
        byCountry.get(g.country).push(g);
      });
      const worthDedicatedRow = c => {
        const countryGroups = byCountry.get(c);
        const distinctBands = new Set(countryGroups.flatMap(g => g.bands)).size;
        const distinctDates = new Set(countryGroups.map(g => g.date)).size;
        return distinctBands > 1 && distinctDates > 1;
      };
      const multiCountries = [...byCountry.keys()]
        .filter(worthDedicatedRow)
        .sort((a, b) => byCountry.get(b).length - byCountry.get(a).length || countryName(a).localeCompare(countryName(b)));
      const singleGroups = [...byCountry.keys()]
        .filter(c => !worthDedicatedRow(c))
        .flatMap(c => byCountry.get(c));

      const grid = document.getElementById('weekGrid');
      grid.innerHTML = "";
      const today = todayISO();

      weekDates.forEach(dateStr => {
        const d = new Date(dateStr + "T00:00:00");
        const header = document.createElement('div');
        header.className = "week-col-header" + (dateStr === today ? " is-today" : "");
        header.innerHTML = `<span>${dowNames[d.getDay()]}</span><span>${d.getDate()} ${monthNames[d.getMonth()]}</span>`;
        grid.appendChild(header);
      });

      if (multiCountries.length === 0 && singleGroups.length === 0) {
        const empty = document.createElement('div');
        empty.className = "week-empty-row";
        empty.textContent = "No shows this week";
        grid.appendChild(empty);
        return;
      }

      multiCountries.forEach(country => {
        const countryGroups = byCountry.get(country);
        const header = document.createElement('div');
        header.className = "week-country-header";
        header.innerHTML = `<span>${countryFlag(country)} ${escapeHtml(countryName(country))}</span><span class="week-country-count">${countryGroups.length}</span>`;
        grid.appendChild(header);
        renderWeekCellRow(grid, weekDates, today, groupByDate(countryGroups), false);
      });

      if (singleGroups.length > 0) {
        const header = document.createElement('div');
        header.className = "week-country-header";
        header.innerHTML = `<span>Others</span><span class="week-country-count">${singleGroups.length}</span>`;
        grid.appendChild(header);
        renderWeekCellRow(grid, weekDates, today, groupByDate(singleGroups), true);
      }
    }

    function renderMapView(list) {
      const mapEl = document.getElementById('mapView');
      if (!leafletMap) {
        leafletMap = L.map(mapEl).setView([54, 15], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '© OpenStreetMap contributors',
          maxZoom: 18,
        }).addTo(leafletMap);
      }

      leafletMarkers.forEach(m => leafletMap.removeLayer(m));
      leafletMarkers = [];

      const byCity = new Map();
      list.forEach(s => {
        const coordKey = `${s.city}|${s.country}`;
        // fall back to a country-level point when the specific city has no
        // geocoded entry (e.g. a small village) -- better to plot it
        // somewhere in the right country than drop it from the map entirely
        const coord = cityCoordinates[coordKey] || countryCoordinates[s.country];
        if (!coord) return;
        if (!byCity.has(coordKey)) byCity.set(coordKey, { coord, city: s.city, country: s.country, shows: [] });
        byCity.get(coordKey).shows.push(s);
      });

      byCity.forEach(entry => {
        const marker = L.circleMarker([entry.coord.lat, entry.coord.lon], {
          radius: 6 + Math.min(entry.shows.length, 10),
          color: '#1E1A17',
          weight: 2,
          fillColor: '#FF3B78',
          fillOpacity: 0.85,
        }).addTo(leafletMap);

        const sortedShows = entry.shows.slice().sort((a, b) => a.date.localeCompare(b.date));
        const popupHtml = `<div class="map-popup"><strong>${countryFlag(entry.country)} ${escapeHtml(entry.city)}</strong>` +
          sortedShows.map(s => `<a class="mp-band" href="${escapeAttr(youtubeMusicUrl(s.band))}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.band)}</a>${s.date} — ${s.url ? `<a href="${escapeAttr(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.venue || 'link')}</a>` : escapeHtml(s.venue)}`).join('') +
          `</div>`;
        marker.bindPopup(popupHtml);
        leafletMarkers.push(marker);
      });

      setTimeout(() => leafletMap.invalidateSize(), 0);
    }

    function resetAllFilters() {
      bandFacet.reset();
      sourceFacet.reset();
      cityFacet.reset();

      // festivals + origins + priority
      excludeFest = false;
      document.getElementById('excludeFest').checked = false;
      activeOrigins.clear();
      document.querySelectorAll('.chip[data-filter="origin"]').forEach(c => c.classList.remove('active'));
      activePriorities.clear();
      document.querySelectorAll('.chip[data-filter="priority"]').forEach(c => c.classList.remove('active'));

      // dates
      document.getElementById('datePreset').value = "all";
      applyDatePreset("all"); // also resets weekStart and calls render()

      // sort
      sortBy = "date";
      document.getElementById('sortBy').value = "date";

      // view
      viewMode = "list";
      document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
      document.querySelector('.view-btn[data-view="list"]').classList.add('active');
      document.getElementById('route').hidden = false;
      document.getElementById('weeklyView').hidden = true;
      document.getElementById('mapViewContainer').hidden = true;

      render();
    }

    function activateResetTitle() { resetAllFilters(); }
    const resetTitleEl = document.getElementById('resetTitle');
    resetTitleEl.addEventListener('click', activateResetTitle);
    resetTitleEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activateResetTitle();
      }
    });

    // ---- EVENT WIRING ----
    document.querySelectorAll('.chip[data-filter="origin"]').forEach(chip => {
      chip.addEventListener('click', () => {
        chip.classList.toggle('active');
        const value = chip.dataset.value;
        if (activeOrigins.has(value)) activeOrigins.delete(value); else activeOrigins.add(value);
        render();
      });
    });

    document.querySelectorAll('.chip[data-filter="priority"]').forEach(chip => {
      chip.addEventListener('click', () => {
        chip.classList.toggle('active');
        const value = chip.dataset.value;
        if (activePriorities.has(value)) activePriorities.delete(value); else activePriorities.add(value);
        render();
      });
    });

    document.getElementById('excludeFest').addEventListener('change', (e) => { excludeFest = e.target.checked; render(); });

    // last date among shows matching the current non-date filters (band/source/city/origin/fest/priority)
    function filteredMaxDate() {
      return getFilteredList("date").reduce((max, s) => s.date > max ? s.date : max, WINDOW_FROM);
    }

    function applyDatePreset(preset) {
      datePreset = preset;
      const customDateRange = document.getElementById('customDateRange');
      let weekAnchorDate = null;

      if (preset === "all") {
        dateFrom = null; dateTo = null;
        customDateRange.hidden = true;
      } else if (preset === "custom") {
        customDateRange.hidden = false;
        dateFrom = document.getElementById('dateFrom').value || null;
        const dateToEl = document.getElementById('dateTo');
        dateToEl.value = filteredMaxDate();
        dateTo = dateToEl.value || null;
        weekAnchorDate = dateFrom;
      } else {
        const holiday = SCHOOL_HOLIDAYS[preset];
        dateFrom = holiday.from; dateTo = holiday.to;
        customDateRange.hidden = true;
        // predefined ranges (school holidays) always show a Saturday-to-
        // Saturday week in the weekly view, regardless of which weekday
        // the holiday itself starts on
        weekAnchorDate = saturdayOnOrAfter(dateFrom);
      }
      // the weekly view's own week pointer is independent of the date filter --
      // jump it to match, otherwise switching to a preset far from the current
      // week leaves the weekly grid showing an empty, unrelated week
      weekStart = weekAnchor = weekAnchorDate || todayISO();
      render();
    }

    document.getElementById('datePreset').addEventListener('change', (e) => applyDatePreset(e.target.value));
    document.getElementById('dateFrom').addEventListener('change', (e) => {
      dateFrom = e.target.value || null;
      weekStart = weekAnchor = dateFrom || todayISO();
      render();
    });
    document.getElementById('dateTo').addEventListener('change', (e) => { dateTo = e.target.value || null; render(); });

    document.getElementById('sortBy').addEventListener('change', (e) => { sortBy = e.target.value; render(); });

    document.querySelectorAll('details.city-filter').forEach(details => {
      details.addEventListener('toggle', () => {
        if (details.open) {
          document.querySelectorAll('details.city-filter').forEach(other => {
            if (other !== details) other.open = false;
          });
          details.querySelector('.filter-search')?.focus();
          if (details.id === "bandFilterDetails") bandFacet.refreshAvailability(new Set(getFilteredList("band").map(s => s.band)));
          else if (details.id === "sourceFilterDetails") sourceFacet.refreshAvailability(new Set(getFilteredList("source").map(s => s.source)));
          else if (details.id === "cityFilterDetails") cityFacet.refreshAvailability(new Set(getFilteredList("city").map(cityKey)));
        }
      });
    });

    document.addEventListener('click', (e) => {
      document.querySelectorAll('details.city-filter[open]').forEach(details => {
        if (!details.contains(e.target)) details.open = false;
      });
      // clicking anywhere outside an open "+N more" popover closes it, same
      // pattern as the filter-panel <details> above
      document.querySelectorAll('.w-more-wrap.is-open').forEach(wrap => {
        if (!wrap.contains(e.target)) wrap.classList.remove('is-open');
      });
    });

    document.querySelectorAll('.view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        viewMode = btn.dataset.view;
        document.getElementById('route').hidden = viewMode !== "list";
        document.getElementById('weeklyView').hidden = viewMode !== "weekly";
        document.getElementById('mapViewContainer').hidden = viewMode !== "map";
        render();
      });
    });

    document.getElementById('weekPrev').addEventListener('click', () => {
      const candidate = candidatePrevWeekStart();
      if (isWeekEntirelyPast(candidate)) return; // previous week would be entirely before today
      weekStart = candidate;
      render();
    });
    document.getElementById('weekNext').addEventListener('click', () => {
      weekStart = mondayStrictlyAfter(weekStart);
      render();
    });
