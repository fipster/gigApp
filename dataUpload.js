
    // ---- DATA ----
    // One entry per confirmed show. Add more bands by adding more entries —
    // the band dropdown and city filter both build themselves from this list.

    let shows = [];
    let countries = {};
    let WINDOW_TO;

    async function loadShows() {
      const [showsResponse, countriesResponse, flightsResponse] = await Promise.all([
        fetch("shows.json"),
        fetch("countries.json"),
        fetch("direct_flights.json"),
      ]);
      shows = await showsResponse.json();
      countries = await countriesResponse.json();
      const flightsData = await flightsResponse.json();

      WINDOW_TO = shows.reduce((max, s) => s.date > max ? s.date : max, WINDOW_FROM);
      dateTo = WINDOW_TO;

      const dateFromEl = document.getElementById('dateFrom');
      const dateToEl = document.getElementById('dateTo');
      dateFromEl.min = WINDOW_FROM;
      dateFromEl.max = WINDOW_TO;
      dateToEl.min = WINDOW_FROM;
      dateToEl.max = WINDOW_TO;
      dateFromEl.value = WINDOW_FROM;
      dateToEl.value = WINDOW_TO;

      const sub = document.querySelector('.sub');
      if (sub && flightsData.gathered_on) {
        sub.insertAdjacentHTML('beforeend', `<br>Flight info gathered on: ${flightsData.gathered_on}`);
      }

      buildBandPanel();
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

    function countryFlag(code) { return countries[code]?.flag || ""; }
    function countryName(code) { return countries[code]?.name || code; }

    let bandMode = "include";
    let bandSelection = new Set();
    let activeOrigins = new Set();
    let excludeFest = false;
    let cityMode = "include";
    let citySelection = new Set();
    let dateFrom = WINDOW_FROM;
    let dateTo = WINDOW_TO;

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function escapeAttr(str) {
      return escapeHtml(str).replace(/"/g, '&quot;');
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
        row.appendChild(cb);
        row.appendChild(document.createTextNode(band));
        container.appendChild(row);

        cb.addEventListener('change', () => {
          if (cb.checked) bandSelection.add(band); else bandSelection.delete(band);
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

    function flightPillHtml(originLabel, direct) {
      return `<div class="flight-pill${direct ? "" : " none"}"><span class="airport-badge">${originLabel}</span></div>`;
    }

    function flightSummaryHtml(s) {
      if (!s.flightTLL.direct && !s.flightRIX.direct) return "";
      const duration = formatDuration(s.flightTLL.duration_minutes);
      if (!duration) return "";
      const seasonal = s.flightTLL.seasonal ? ` (seasonal${s.flightTLL.seasonal_months ? `, ${s.flightTLL.seasonal_months}` : ""})` : "";
      return `<span class="flight-duration">${duration}${seasonal}</span>`;
    }

    function render() {
      const uniqueBands = [...new Set(shows.map(s => s.band))].sort();
      let list = shows.slice();

      if (bandMode === "include") {
        list = list.filter(s => bandSelection.has(s.band));
      } else {
        list = list.filter(s => !bandSelection.has(s.band));
      }
      if (excludeFest) list = list.filter(s => !s.fest);
      list = list.filter(s => s.date >= dateFrom && s.date <= dateTo);
      if (cityMode === "include") {
        list = list.filter(s => citySelection.has(cityKey(s)));
      } else {
        list = list.filter(s => !citySelection.has(cityKey(s)));
      }
      if (activeOrigins.size > 0) list = list.filter(s => hasDirectForSelection(s));

      list.sort((a, b) => a.date.localeCompare(b.date));

      document.getElementById('totalCount').textContent = shows.length + " confirmed";
      document.getElementById('windowLabel').textContent = formatWindowLabel(WINDOW_FROM, WINDOW_TO);

      const activeBandCount = bandMode === "include" ? bandSelection.size : uniqueBands.length - bandSelection.size;
      document.getElementById('bandFilterCount').textContent = `${activeBandCount}/${uniqueBands.length} bands`;

      document.getElementById('cityFilterCount').textContent = `${list.length}/${shows.length} shows`;

      const root = document.getElementById('route');
      root.innerHTML = "";

      if (list.length === 0) {
        root.innerHTML = '<div class="empty">NO SHOWS MATCH THESE FILTERS</div>';
        return;
      }

      let lastMonth = null;
      list.forEach(s => {
        const d = new Date(s.date + "T00:00:00");
        const monthKey = monthNames[d.getMonth()] + " " + d.getFullYear();

        if (monthKey !== lastMonth) {
          const div = document.createElement('div');
          div.className = "month-divider";
          div.textContent = monthKey;
          root.appendChild(div);
          lastMonth = monthKey;
        }

        const stub = document.createElement('div');
        stub.className = "stub";

        let flightsHtml = "";
        flightsHtml += flightPillHtml("TLL", s.flightTLL.direct);
        flightsHtml += flightPillHtml("RIX", s.flightRIX.direct);
        flightsHtml += flightSummaryHtml(s);

        stub.innerHTML = `
      <div class="stub-dot"></div>
      <div class="date-block">
        <div class="dow">${dowNames[d.getDay()]}</div>
        <div class="dnum">${d.getDate()}</div>
        <div class="mon">${monthNames[d.getMonth()]} '${String(d.getFullYear()).slice(2)}</div>
      </div>
      <div class="stub-body">
        <div class="stub-top">
          <span class="band-tag ${bandColorClass(s.band, uniqueBands)}">${escapeHtml(s.band)}</span>
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
    document.querySelectorAll('.chip[data-filter="origin"]').forEach(chip => {
      chip.addEventListener('click', () => {
        chip.classList.toggle('active');
        const value = chip.dataset.value;
        if (activeOrigins.has(value)) activeOrigins.delete(value); else activeOrigins.add(value);
        render();
      });
    });

    document.getElementById('excludeFest').addEventListener('change', (e) => { excludeFest = e.target.checked; render(); });

    document.getElementById('dateFrom').addEventListener('change', (e) => { dateFrom = e.target.value || WINDOW_FROM; render(); });
    document.getElementById('dateTo').addEventListener('change', (e) => { dateTo = e.target.value || WINDOW_TO; render(); });
    document.getElementById('resetDates').addEventListener('click', () => {
      dateFrom = WINDOW_FROM; dateTo = WINDOW_TO;
      document.getElementById('dateFrom').value = dateFrom;
      document.getElementById('dateTo').value = dateTo;
      render();
    });

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