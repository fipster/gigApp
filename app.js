
    // ---- DATA ----
    // One entry per confirmed show. Add more bands by adding more entries —
    // the band dropdown and city filter both build themselves from this list.

    let shows = [];
    let countries = {};
    let WINDOW_TO;

    async function loadShows() {
      const [showsResponse, countriesResponse] = await Promise.all([
        fetch("shows.json"),
        fetch("countries.json"),
      ]);
      shows = await showsResponse.json();
      countries = await countriesResponse.json();

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

    function render() {
      const uniqueBands = [...new Set(shows.map(s => s.band))].sort();
      const uniqueSources = [...new Set(shows.map(s => s.source))].sort();
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

      // group shows sharing the same date + venue (e.g. festival lineups) into one row
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

      if (sortBy === "bandCount") {
        groups.sort((a, b) => b.bands.length - a.bands.length || a.date.localeCompare(b.date));
      } else {
        groups.sort((a, b) => a.date.localeCompare(b.date));
      }

      document.getElementById('totalCount').textContent = shows.length + " confirmed";
      document.getElementById('windowLabel').textContent = formatWindowLabel(WINDOW_FROM, WINDOW_TO);

      const activeBandCount = new Set(list.map(s => s.band)).size;
      document.getElementById('bandFilterCount').textContent = `${activeBandCount}/${uniqueBands.length} bands`;

      const activeSourceCount = new Set(list.map(s => s.source)).size;
      document.getElementById('sourceFilterCount').textContent = `${activeSourceCount}/${uniqueSources.length} sources`;

      document.getElementById('cityFilterCount').textContent = `${list.length}/${shows.length} shows`;

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

    function applyDatePreset(preset) {
      datePreset = preset;
      const customDateRange = document.getElementById('customDateRange');

      if (preset === "all") {
        dateFrom = null; dateTo = null;
        customDateRange.hidden = true;
      } else if (preset === "custom") {
        customDateRange.hidden = false;
        dateFrom = document.getElementById('dateFrom').value || null;
        dateTo = document.getElementById('dateTo').value || null;
      } else {
        const holiday = SCHOOL_HOLIDAYS[preset];
        dateFrom = holiday.from; dateTo = holiday.to;
        customDateRange.hidden = true;
      }
      render();
    }

    document.getElementById('datePreset').addEventListener('change', (e) => applyDatePreset(e.target.value));
    document.getElementById('dateFrom').addEventListener('change', (e) => { dateFrom = e.target.value || null; render(); });
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
        }
      });
    });

    document.addEventListener('click', (e) => {
      document.querySelectorAll('details.city-filter[open]').forEach(details => {
        if (!details.contains(e.target)) details.open = false;
      });
    });

    //buildBandPanel();
    //buildCityPanel();
    //render();