
    // ---- DATA ----
    // One entry per confirmed show. Add more bands by adding more entries —
    // the band dropdown and city filter both build themselves from this list.

    let shows = [];
    let countries = {};
    let cityCoordinates = {};
    let WINDOW_TO;

    async function loadShows() {
      const [showsResponse, countriesResponse, coordsResponse] = await Promise.all([
        fetch("shows.json"),
        fetch("countries.json"),
        fetch("city_coordinates.json"),
      ]);
      shows = await showsResponse.json();
      countries = await countriesResponse.json();
      cityCoordinates = await coordsResponse.json();

      WINDOW_TO = shows.reduce((max, s) => s.date > max ? s.date : max, WINDOW_FROM);

      const dateFromEl = document.getElementById('dateFrom');
      const dateToEl = document.getElementById('dateTo');
      dateFromEl.min = WINDOW_FROM;
      dateFromEl.max = WINDOW_TO;
      dateToEl.min = WINDOW_FROM;
      dateToEl.max = WINDOW_TO;
      dateFromEl.value = WINDOW_FROM;
      dateToEl.value = WINDOW_TO;

      buildBandPanel();
      buildSourcePanel();
      buildCityPanel();
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

    function mondayOf(dateStr) {
      const d = new Date(dateStr + "T00:00:00");
      d.setDate(d.getDate() - (d.getDay() + 6) % 7);
      return toISO(d);
    }

    function addDays(dateStr, days) {
      const d = new Date(dateStr + "T00:00:00");
      d.setDate(d.getDate() + days);
      return toISO(d);
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
    let excludeFest = false;
    let cityMode = "include";
    let citySelection = new Set();
    let datePreset = "all";
    let dateFrom = null;
    let dateTo = null;
    let sortBy = "date";
    let viewMode = "list";
    let weekStart = mondayOf(todayISO());
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

    function bandColorClass(bandName, uniqueBands) {
      return "b" + (uniqueBands.indexOf(bandName) % 4);
    }

    function cityKey(s) { return s.country + "|" + s.city; }

    // ---- BAND DROPDOWN (built dynamically) ----
    function buildBandPanel() {
      const uniqueBands = [...new Set(shows.map(s => s.band))].sort();
      uniqueBands.forEach(band => bandSelection.add(band));

      const container = document.getElementById('bandCheckboxList');
      container.innerHTML = "";

      uniqueBands.forEach(band => {
        const row = document.createElement('label');
        row.className = "city-row";
        row.style.paddingLeft = "0";
        const cb = document.createElement('input');
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.band = band;
        row.appendChild(cb);
        row.appendChild(document.createTextNode(band));
        container.appendChild(row);

        cb.addEventListener('change', () => {
          if (cb.checked) bandSelection.add(band); else bandSelection.delete(band);
          render();
        });
      });
    }

    // ---- SOURCE FILTER (built dynamically) ----
    function buildSourcePanel() {
      const uniqueSources = [...new Set(shows.map(s => s.source))].sort();
      uniqueSources.forEach(source => sourceSelection.add(source));

      const container = document.getElementById('sourceCheckboxList');
      container.innerHTML = "";

      uniqueSources.forEach(source => {
        const row = document.createElement('label');
        row.className = "city-row";
        row.style.paddingLeft = "0";
        const cb = document.createElement('input');
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.source = source;
        row.appendChild(cb);
        row.appendChild(document.createTextNode(source));
        container.appendChild(row);

        cb.addEventListener('change', () => {
          if (cb.checked) sourceSelection.add(source); else sourceSelection.delete(source);
          render();
        });
      });
    }

    // ---- CITY / COUNTRY FILTER PANEL ----
    function buildCityPanel() {
      const byCountry = {};
      const order = [];
      shows.forEach(s => {
        if (!byCountry[s.country]) { byCountry[s.country] = []; order.push(s.country); }
        if (!byCountry[s.country].some(c => c.city === s.city)) {
          byCountry[s.country].push({ city: s.city });
        }
        citySelection.add(cityKey(s));
      });

      order.sort((a, b) => countryName(a).localeCompare(countryName(b)));
      Object.values(byCountry).forEach(cities => cities.sort((a, b) => a.city.localeCompare(b.city)));

      const container = document.getElementById('cityCheckboxList');
      container.innerHTML = "";

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

        const cityRows = [];
        cities.forEach(c => {
          const key = country + "|" + c.city;
          const cityRow = document.createElement('label');
          cityRow.className = "city-row";
          const cityCb = document.createElement('input');
          cityCb.type = "checkbox";
          cityCb.checked = true;
          cityCb.dataset.key = key;
          cityRow.appendChild(cityCb);
          cityRow.appendChild(document.createTextNode(c.city));
          group.appendChild(cityRow);
          cityRows.push(cityCb);

          cityCb.addEventListener('change', () => {
            if (cityCb.checked) citySelection.add(key); else citySelection.delete(key);
            countryCb.checked = cityRows.every(cb => cb.checked);
            render();
          });
        });

        countryCb.addEventListener('change', () => {
          cityRows.forEach(cb => {
            cb.checked = countryCb.checked;
            const key = country + "|" + cities[cityRows.indexOf(cb)].city;
            if (cb.checked) citySelection.add(key); else citySelection.delete(key);
          });
          render();
        });

        container.appendChild(group);
      });
    }

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

    function getFilteredList() {
      let list = shows.slice();

      if (bandMode === "include") {
        list = list.filter(s => bandSelection.has(s.band));
      } else {
        list = list.filter(s => !bandSelection.has(s.band));
      }
      if (excludeFest) list = list.filter(s => !s.fest);
      if (dateFrom) list = list.filter(s => s.date >= dateFrom);
      if (dateTo) list = list.filter(s => s.date <= dateTo);
      if (sourceMode === "include") {
        list = list.filter(s => sourceSelection.has(s.source));
      } else {
        list = list.filter(s => !sourceSelection.has(s.source));
      }
      if (cityMode === "include") {
        list = list.filter(s => citySelection.has(cityKey(s)));
      } else {
        list = list.filter(s => !citySelection.has(cityKey(s)));
      }
      if (activeOrigins.size > 0) list = list.filter(s => hasDirectForSelection(s));
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
      const uniqueBands = [...new Set(shows.map(s => s.band))].sort();
      const uniqueSources = [...new Set(shows.map(s => s.source))].sort();
      const list = getFilteredList();

      document.getElementById('totalCount').textContent = shows.length + " confirmed";
      document.getElementById('windowLabel').textContent = formatWindowLabel(WINDOW_FROM, WINDOW_TO);

      const activeBandCount = new Set(list.map(s => s.band)).size;
      document.getElementById('bandFilterCount').textContent = `${activeBandCount}/${uniqueBands.length} bands`;

      const activeSourceCount = new Set(list.map(s => s.source)).size;
      document.getElementById('sourceFilterCount').textContent = `${activeSourceCount}/${uniqueSources.length} sources`;

      document.getElementById('cityFilterCount').textContent = `${list.length}/${shows.length} shows`;

      if (viewMode === "list") renderListView(list, uniqueBands);
      else if (viewMode === "weekly") renderWeeklyView(list, uniqueBands);
      else if (viewMode === "map") renderMapView(list);
    }

    function renderListView(list, uniqueBands) {
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
          `<a class="band-tag ${bandColorClass(band, uniqueBands)}" href="${escapeAttr("https://music.youtube.com/search?q=" + encodeURIComponent(band))}" target="_blank" rel="noopener noreferrer">${escapeHtml(band)}</a>`
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
      const sameMonth = from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear();
      const fromStr = `${from.getDate()} ${monthNames[from.getMonth()]}`;
      const toStr = sameMonth ? `${to.getDate()} ${monthNames[to.getMonth()]}` : `${to.getDate()} ${monthNames[to.getMonth()]} ${to.getFullYear()}`;
      return `${fromStr} – ${toStr} ${to.getFullYear()}`;
    }

    function renderWeeklyView(list, uniqueBands) {
      document.getElementById('weekLabel').textContent = formatWeekLabel(weekStart);

      const groups = groupShows(list);
      const byDay = {};
      for (let i = 0; i < 7; i++) byDay[addDays(weekStart, i)] = [];
      groups.forEach(g => { if (byDay[g.date]) byDay[g.date].push(g); });

      const grid = document.getElementById('weekGrid');
      grid.innerHTML = "";
      const today = todayISO();

      for (let i = 0; i < 7; i++) {
        const dateStr = addDays(weekStart, i);
        const d = new Date(dateStr + "T00:00:00");
        const dayShows = byDay[dateStr];

        const dayEl = document.createElement('div');
        dayEl.className = "week-day" + (dateStr === today ? " is-today" : "");

        const header = document.createElement('div');
        header.className = "week-day-header";
        header.innerHTML = `<span>${dowNames[d.getDay()]}</span><span>${d.getDate()} ${monthNames[d.getMonth()]}</span>`;
        dayEl.appendChild(header);

        const body = document.createElement('div');
        body.className = "week-day-body";

        if (dayShows.length === 0) {
          body.innerHTML = '<div class="week-empty">—</div>';
        } else {
          dayShows.forEach(s => {
            const bandLabel = s.bands.length > 1 ? `${s.bands[0]} +${s.bands.length - 1}` : s.bands[0];
            const entry = document.createElement(s.url ? 'a' : 'div');
            entry.className = "week-show-entry";
            if (s.url) {
              entry.href = s.url;
              entry.target = "_blank";
              entry.rel = "noopener noreferrer";
            }
            entry.innerHTML = `<span class="w-band">${escapeHtml(bandLabel)}</span><span class="w-city">${countryFlag(s.country)} ${escapeHtml(s.city)}</span>`;
            body.appendChild(entry);
          });
        }

        dayEl.appendChild(body);
        grid.appendChild(dayEl);
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
        const coord = cityCoordinates[coordKey];
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
          sortedShows.map(s => `<span class="mp-band">${escapeHtml(s.band)}</span>${s.date} — ${s.url ? `<a href="${escapeAttr(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.venue || 'link')}</a>` : escapeHtml(s.venue)}`).join('') +
          `</div>`;
        marker.bindPopup(popupHtml);
        leafletMarkers.push(marker);
      });

      setTimeout(() => leafletMap.invalidateSize(), 0);
    }

    function resetAllFilters() {
      // bands
      bandMode = "include";
      bandSelection.clear();
      [...new Set(shows.map(s => s.band))].forEach(b => bandSelection.add(b));
      document.querySelectorAll('#bandFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
      document.querySelector('#bandFilterDetails .mode-row .chip[data-bandmode="include"]').classList.add('active');
      document.getElementById('bandSearchInput').value = "";
      document.querySelectorAll('#bandCheckboxList .city-row').forEach(row => {
        row.style.display = "";
        row.querySelector('input[type=checkbox]').checked = true;
      });

      // sources
      sourceMode = "include";
      sourceSelection.clear();
      [...new Set(shows.map(s => s.source))].forEach(src => sourceSelection.add(src));
      document.querySelectorAll('#sourceFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
      document.querySelector('#sourceFilterDetails .mode-row .chip[data-sourcemode="include"]').classList.add('active');
      document.getElementById('sourceSearchInput').value = "";
      document.querySelectorAll('#sourceCheckboxList .city-row').forEach(row => {
        row.style.display = "";
        row.querySelector('input[type=checkbox]').checked = true;
      });

      // cities
      cityMode = "include";
      citySelection.clear();
      shows.forEach(s => citySelection.add(cityKey(s)));
      document.querySelectorAll('#cityFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
      document.querySelector('#cityFilterDetails .mode-row .chip[data-mode="include"]').classList.add('active');
      document.getElementById('citySearchInput').value = "";
      document.querySelectorAll('#cityCheckboxList .country-group').forEach(group => group.style.display = "");
      document.querySelectorAll('#cityCheckboxList .city-row, #cityCheckboxList .country-row').forEach(row => {
        row.style.display = "";
        row.querySelector('input[type=checkbox]').checked = true;
      });

      // festivals + origins
      excludeFest = false;
      document.getElementById('excludeFest').checked = false;
      activeOrigins.clear();
      document.querySelectorAll('.chip[data-filter="origin"]').forEach(c => c.classList.remove('active'));

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

    document.getElementById('resetTitle').addEventListener('click', resetAllFilters);

    // ---- EVENT WIRING ----
    document.getElementById('bandSearchInput').addEventListener('input', (e) => {
      const rawInput = e.target.value;
      document.querySelectorAll('#bandCheckboxList .city-row').forEach(row => {
        const cb = row.querySelector('input[type=checkbox]');
        const matches = matchesSearchTerms(cb.dataset.band, rawInput);
        row.style.display = matches ? "" : "none";
        cb.checked = matches;
        if (matches) bandSelection.add(cb.dataset.band); else bandSelection.delete(cb.dataset.band);
      });
      render();
    });

    document.getElementById('sourceSearchInput').addEventListener('input', (e) => {
      const rawInput = e.target.value;
      document.querySelectorAll('#sourceCheckboxList .city-row').forEach(row => {
        const cb = row.querySelector('input[type=checkbox]');
        const matches = matchesSearchTerms(cb.dataset.source, rawInput);
        row.style.display = matches ? "" : "none";
        cb.checked = matches;
        if (matches) sourceSelection.add(cb.dataset.source); else sourceSelection.delete(cb.dataset.source);
      });
      render();
    });

    document.getElementById('citySearchInput').addEventListener('input', (e) => {
      const rawInput = e.target.value;
      document.querySelectorAll('#cityCheckboxList .country-group').forEach(group => {
        const countryRow = group.querySelector('.country-row');
        const countryCb = countryRow.querySelector('input[type=checkbox]');
        const cityRows = [...group.querySelectorAll('.city-row')];
        const countryMatches = matchesSearchTerms(countryRow.textContent, rawInput);
        let anyCityMatches = false;
        cityRows.forEach(row => {
          const cb = row.querySelector('input[type=checkbox]');
          const matches = countryMatches || matchesSearchTerms(row.textContent, rawInput);
          row.style.display = matches ? "" : "none";
          cb.checked = matches;
          if (matches) { citySelection.add(cb.dataset.key); anyCityMatches = true; }
          else citySelection.delete(cb.dataset.key);
        });
        group.style.display = (countryMatches || anyCityMatches) ? "" : "none";
        countryCb.checked = cityRows.every(row => row.querySelector('input[type=checkbox]').checked);
      });
      render();
    });

    document.querySelectorAll('.chip[data-filter="origin"]').forEach(chip => {
      chip.addEventListener('click', () => {
        chip.classList.toggle('active');
        const value = chip.dataset.value;
        if (activeOrigins.has(value)) activeOrigins.delete(value); else activeOrigins.add(value);
        render();
      });
    });

    document.getElementById('excludeFest').addEventListener('change', (e) => { excludeFest = e.target.checked; render(); });

    // last date among shows matching the current non-date filters (band/source/city/origin/fest)
    function filteredMaxDate() {
      let list = shows.slice();
      if (bandMode === "include") {
        list = list.filter(s => bandSelection.has(s.band));
      } else {
        list = list.filter(s => !bandSelection.has(s.band));
      }
      if (excludeFest) list = list.filter(s => !s.fest);
      if (sourceMode === "include") {
        list = list.filter(s => sourceSelection.has(s.source));
      } else {
        list = list.filter(s => !sourceSelection.has(s.source));
      }
      if (cityMode === "include") {
        list = list.filter(s => citySelection.has(cityKey(s)));
      } else {
        list = list.filter(s => !citySelection.has(cityKey(s)));
      }
      if (activeOrigins.size > 0) list = list.filter(s => hasDirectForSelection(s));

      return list.reduce((max, s) => s.date > max ? s.date : max, WINDOW_FROM);
    }

    function applyDatePreset(preset) {
      datePreset = preset;
      const customDateRange = document.getElementById('customDateRange');

      if (preset === "all") {
        dateFrom = null; dateTo = null;
        customDateRange.hidden = true;
      } else if (preset === "custom") {
        customDateRange.hidden = false;
        dateFrom = document.getElementById('dateFrom').value || null;
        const dateToEl = document.getElementById('dateTo');
        dateToEl.value = filteredMaxDate();
        dateTo = dateToEl.value || null;
      } else {
        const holiday = SCHOOL_HOLIDAYS[preset];
        dateFrom = holiday.from; dateTo = holiday.to;
        customDateRange.hidden = true;
      }
      // the weekly view's own week pointer is independent of the date filter --
      // jump it to match, otherwise switching to a preset far from the current
      // week leaves the weekly grid showing an empty, unrelated week
      weekStart = mondayOf(dateFrom || todayISO());
      render();
    }

    document.getElementById('datePreset').addEventListener('change', (e) => applyDatePreset(e.target.value));
    document.getElementById('dateFrom').addEventListener('change', (e) => {
      dateFrom = e.target.value || null;
      weekStart = mondayOf(dateFrom || todayISO());
      render();
    });
    document.getElementById('dateTo').addEventListener('change', (e) => { dateTo = e.target.value || null; render(); });

    document.getElementById('sortBy').addEventListener('change', (e) => { sortBy = e.target.value; render(); });

    document.querySelectorAll('#cityFilterDetails .mode-row .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('#cityFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        cityMode = chip.dataset.mode;
        render();
      });
    });

    document.getElementById('selectAllCities').addEventListener('click', () => {
      document.querySelectorAll('#cityCheckboxList input[type=checkbox]').forEach(cb => cb.checked = true);
      citySelection.clear();
      shows.forEach(s => citySelection.add(cityKey(s)));
      render();
    });
    document.getElementById('selectNoneCities').addEventListener('click', () => {
      document.querySelectorAll('#cityCheckboxList input[type=checkbox]').forEach(cb => cb.checked = false);
      citySelection.clear();
      render();
    });

    document.querySelectorAll('#bandFilterDetails .mode-row .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('#bandFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        bandMode = chip.dataset.bandmode;
        render();
      });
    });

    document.getElementById('selectAllBands').addEventListener('click', () => {
      document.querySelectorAll('#bandCheckboxList input[type=checkbox]').forEach(cb => cb.checked = true);
      bandSelection.clear();
      [...new Set(shows.map(s => s.band))].forEach(b => bandSelection.add(b));
      render();
    });
    document.getElementById('selectNoneBands').addEventListener('click', () => {
      document.querySelectorAll('#bandCheckboxList input[type=checkbox]').forEach(cb => cb.checked = false);
      bandSelection.clear();
      render();
    });

    document.querySelectorAll('#sourceFilterDetails .mode-row .chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('#sourceFilterDetails .mode-row .chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        sourceMode = chip.dataset.sourcemode;
        render();
      });
    });

    document.getElementById('selectAllSources').addEventListener('click', () => {
      document.querySelectorAll('#sourceCheckboxList input[type=checkbox]').forEach(cb => cb.checked = true);
      sourceSelection.clear();
      [...new Set(shows.map(s => s.source))].forEach(src => sourceSelection.add(src));
      render();
    });
    document.getElementById('selectNoneSources').addEventListener('click', () => {
      document.querySelectorAll('#sourceCheckboxList input[type=checkbox]').forEach(cb => cb.checked = false);
      sourceSelection.clear();
      render();
    });

    document.querySelectorAll('details.city-filter').forEach(details => {
      details.addEventListener('toggle', () => {
        if (details.open) {
          document.querySelectorAll('details.city-filter').forEach(other => {
            if (other !== details) other.open = false;
          });
          details.querySelector('.filter-search')?.focus();
        }
      });
    });

    document.addEventListener('click', (e) => {
      document.querySelectorAll('details.city-filter[open]').forEach(details => {
        if (!details.contains(e.target)) details.open = false;
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
      weekStart = addDays(weekStart, -7);
      render();
    });
    document.getElementById('weekNext').addEventListener('click', () => {
      weekStart = addDays(weekStart, 7);
      render();
    });

    //buildBandPanel();
    //buildCityPanel();
    //render();