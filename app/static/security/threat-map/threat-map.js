(function () {
  const FAMILY = {
    brute_force: { color: "#ff2d3a", label: "Brute force", style: "strike" },
    attack_path_probe: { color: "#ffb020", label: "Path probe", style: "scan" },
    honeypot: { color: "#e040fb", label: "Honeypot", style: "trap" },
    token_attack: { color: "#00e5ff", label: "Token attack", style: "spike" },
    csrf: { color: "#18ffff", label: "CSRF", style: "spike" },
    xss: { color: "#c6ff00", label: "XSS", style: "spike" },
    rate_limit: { color: "#ff8a3d", label: "Rate limit", style: "storm" },
    known_attacker_ua: { color: "#f0f4f8", label: "Scanner UA", style: "scan" },
    bot_attempt: { color: "#b0bec5", label: "Bot", style: "scan" },
    suspicious_ua: { color: "#eceff1", label: "Suspicious UA", style: "scan" },
    ddos_attempts: { color: "#ff1744", label: "DDoS", style: "storm" },
    failed_login: { color: "#ff6f91", label: "Failed login", style: "strike" },
    banned_device_block: { color: "#ff6d00", label: "Banned device", style: "strike" },
    banned_ip_block: { color: "#d500f9", label: "Banned IP", style: "strike" },
    reputation_block: { color: "#ff5252", label: "Reputation block", style: "strike" },
    low_reputation: { color: "#ff8a80", label: "Low reputation", style: "spike" },
    n1_attack: { color: "#82b1ff", label: "Query flood", style: "storm" },
    security_test_would_block: { color: "#ffd54f", label: "Would-block (test)", style: "spike" },
    other: { color: "#4dabf7", label: "Other", style: "spike" },
  };

  // AEGIS ops home — Odessa, Texas
  const HOME = { lon: -102.3676, lat: 31.8457, label: "ODESSA, TX" };

  // Geographic centers used if the topojson centroid miss-fires.
  const FALLBACK_LL = {
    US: [-98.35, 39.5], CA: [-106.3, 56.1], MX: [-102.5, 23.6], BR: [-51.9, -14.2],
    AR: [-63.6, -38.4], GB: [-1.5, 52.4], DE: [10.4, 51.1], FR: [2.2, 46.2],
    NL: [5.3, 52.1], BE: [4.5, 50.5], ES: [-3.7, 40.4], IT: [12.6, 42.5],
    PL: [19.4, 52.1], SE: [18.6, 60.1], NO: [8.5, 60.5], FI: [25.7, 61.9],
    RU: [37.6, 55.8], UA: [31.2, 48.4], TR: [35.2, 39.0], IL: [34.8, 31.0],
    AE: [53.8, 23.4], SA: [45.1, 23.9], IN: [78.9, 21.8], CN: [104.2, 35.9],
    HK: [114.2, 22.3], TW: [121.0, 23.7], JP: [138.3, 36.2], KR: [127.8, 36.4],
    SG: [103.8, 1.35], AU: [133.8, -25.3], NZ: [174.8, -41.3], ZA: [24.7, -28.5],
    NG: [8.1, 9.1], EG: [30.8, 26.8], KE: [37.9, -0.02], VN: [108.3, 14.1],
    TH: [100.5, 15.9], ID: [113.9, -0.8], PH: [121.8, 12.9], PK: [69.3, 30.4],
    BD: [90.3, 23.7], IR: [53.7, 32.4], IQ: [43.7, 33.2], RO: [24.97, 45.94],
    CZ: [15.5, 49.8], AT: [14.5, 47.5], CH: [8.2, 46.8], PT: [-8.2, 39.4],
    IE: [-8.2, 53.3], DK: [10.0, 56.0], HU: [19.5, 47.2], GR: [21.8, 39.1],
    CO: [-74.3, 4.6], CL: [-71.5, -35.7], PE: [-75.0, -9.2], VE: [-66.6, 6.4],
    XX: [-40, 20], ZZ: [-90, 25],
  };

  const state = {
    window: "live",
    drawerMode: "window",
    countries: {},
    isoMap: {},
    centroids: {},
    homeXY: null,
    path: null,
    projection: null,
    liveTimer: null,
    replayTimer: null,
    sinceId: -1,
    fx: [],
    selected: null,
    replay: [],
    replayI: 0,
    playing: false,
    speed: 1,
    liveQueue: [],
    livePlayTimer: null,
    armed: false,
    pulse: null,
    lastPollOk: false,
    onReplayDone: null,
    radarT: 0,
    features: [],
    isoNames: {},
    nameToIso: {},
    summary: null,
    recent: [],
    seenIps: {},
    authFails: 0,
    pollInFlight: false,
    awake: false,
    booted: false,
    fxRaf: 0,
  };
  let pageCtl = new AbortController();

  const svg = d3.select("#tm-svg");
  const canvas = document.getElementById("tm-fx");
  const ctx = canvas.getContext("2d", { alpha: true });
  const drawer = document.getElementById("tm-drawer");
  const banner = document.getElementById("tm-banner");
  const bannerTag = document.getElementById("tm-banner-tag");
  const bannerMsg = document.getElementById("tm-banner-msg");
  const playBtn = document.getElementById("tm-play");
  const playStatus = document.getElementById("tm-play-status");
  const feed = document.getElementById("tm-feed");
  const armBox = document.getElementById("tm-arm");
  const armWord = document.getElementById("tm-arm-word");
  const armSub = document.getElementById("tm-arm-sub");
  const armLink = document.getElementById("tm-arm-link");
  let bannerHide = 0;
  let armHold = 0;
  // Live notices: ~10s each. Only cut short when the next hit is ready to show.
  // Last hit in a burst holds longer so CONTACT + arc animations can finish.
  const BANNER_MS = 10000;
  const BANNER_LAST_MS = 18000;
  const HOLD_MS = BANNER_MS;
  let audioCtx = null;

  function unlockAudio() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) {
      /* ignore */
    }
  }

  function customAlertUrl() {
    const wrap = document.getElementById("tm-wrap");
    return (wrap && wrap.getAttribute("data-tm-alert")) || "";
  }

  let alertEl = null;
  function playCustomAlert() {
    const url = customAlertUrl();
    if (!url) return false;
    try {
      if (!alertEl || alertEl.getAttribute("src") !== url) {
        alertEl = new Audio(url);
        alertEl.preload = "auto";
      }
      alertEl.pause();
      alertEl.currentTime = 0;
      const p = alertEl.play();
      if (p && p.catch) {
        p.catch(function () {
          playBeep();
        });
      }
      return true;
    } catch (e) {
      return false;
    }
  }

  function playContactAlert() {
    unlockAudio();
    if (playCustomAlert()) return;
    playBeep();
  }

  function playBeep() {
    try {
      unlockAudio();
      if (!audioCtx) return;
      const t = audioCtx.currentTime;
      const blip = (freq, at, dur, vol) => {
        const osc = audioCtx.createOscillator();
        const g = audioCtx.createGain();
        osc.type = "square";
        osc.frequency.setValueAtTime(freq, t + at);
        g.gain.setValueAtTime(0.0001, t + at);
        g.gain.exponentialRampToValueAtTime(vol, t + at + 0.018);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + dur);
        osc.connect(g);
        g.connect(audioCtx.destination);
        osc.start(t + at);
        osc.stop(t + at + dur + 0.02);
      };
      blip(880, 0, 0.22, 0.11);
      blip(1175, 0.09, 0.2, 0.09);
      blip(988, 0.28, 0.28, 0.1);
    } catch (e) {
      /* ignore */
    }
  }

  document.addEventListener("pointerdown", unlockAudio);
  document.addEventListener("keydown", unlockAudio);
  document.addEventListener("click", function (ev) {
    const t = ev.target;
    if (!t || !t.closest || !t.closest(".tm-alert-test")) return;
    unlockAudio();
    playContactAlert();
  });

  function liveDrainPending() {
    return !!(state.liveQueue.length || state.livePlayTimer);
  }

  function famMeta(name) {
    if (FAMILY[name]) return FAMILY[name];
    const raw = String(name || "other").trim() || "other";
    if (raw === "other") return FAMILY.other;
    const label = raw.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    return { color: "#4dabf7", label: label, style: "spike" };
  }

  function sizeCanvas() {
    const stage = document.querySelector(".tm-stage");
    const w = Math.max(320, stage.clientWidth || 800);
    const h = Math.max(280, stage.clientHeight || 520);
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    svg.attr("viewBox", "0 0 " + w + " " + h);
    return { w, h };
  }

  async function loadIso() {
    const res = await fetch("/static/security/threat-map/data/iso-numeric-to-iso2.json");
    const raw = await res.json();
    state.isoMap = {};
    Object.keys(raw).forEach((k) => {
      const v = raw[k];
      state.isoMap[k] = v;
      const n = parseInt(k, 10);
      if (!isFinite(n)) return;
      state.isoMap[String(n)] = v;
      state.isoMap[String(n).padStart(3, "0")] = v;
    });
  }

  function isoOf(d) {
    if (!d) return "";
    const raw = d.id == null ? "" : String(d.id);
    if (state.isoMap[raw]) return state.isoMap[raw];
    const n = parseInt(raw, 10);
    if (isFinite(n)) {
      if (state.isoMap[String(n)]) return state.isoMap[String(n)];
      const pad = String(n).padStart(3, "0");
      if (state.isoMap[pad]) return state.isoMap[pad];
    }
    const name = (d.properties && d.properties.name) || "";
    return state.nameToIso[name] || "";
  }

  function countryName(iso) {
    if (!iso || iso === "XX") return "Unknown country";
    if (iso === "ZZ") return "Private / local";
    return state.isoNames[iso] || iso;
  }

  function originLabel(iso) {
    if (!iso || iso === "XX") return "??";
    if (iso === "ZZ") return "LAN";
    return iso;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function userLabel(ev) {
    if (!ev) return "";
    const name = ev.username ? String(ev.username) : "";
    const id = ev.user_id ? String(ev.user_id) : "";
    if (name && id) return "@" + name + " #" + id;
    if (name) return "@" + name;
    if (id) return "#" + id;
    return "";
  }

  function userBit(ev) {
    const label = userLabel(ev);
    if (!label) return "";
    return '<i class="tm-user">' + esc(label) + "</i>";
  }

  function userPlain(ev) {
    const label = userLabel(ev);
    return label ? "  ·  " + label : "";
  }

  function heatColor(count, max) {
    if (!count) return "#0d1620";
    // Floor so a single hit is already warm, not navy-on-navy.
    const t = Math.min(1, Math.log10(count + 1) / Math.log10((max || 1) + 1));
    const u = 0.32 + t * 0.68;
    const r = Math.round(170 + u * 85);
    const g = Math.round(28 + u * 78);
    const b = Math.round(22 + (1 - u) * 18);
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  function heatBand(count, max) {
    if (!count) return "";
    const t = Math.min(1, Math.log10(count + 1) / Math.log10((max || 1) + 1));
    if (t >= 0.66) return "hi";
    if (t >= 0.33) return "mid";
    return "lo";
  }

  function paintCountries() {
    const max = d3.max(Object.values(state.countries), (d) => d.count) || 1;
    svg.selectAll("path.tm-land").each(function (d) {
      const iso = isoOf(d);
      const row = iso ? state.countries[iso] : null;
      const count = row ? row.count : 0;
      const band = heatBand(count, max);
      d3.select(this)
        .classed("is-hot", !!count)
        .classed("is-hot-lo", band === "lo")
        .classed("is-hot-mid", band === "mid")
        .classed("is-hot-hi", band === "hi")
        .attr("data-iso", iso)
        .attr("fill", count ? heatColor(count, max) : "#0d1620");
    });
    svg.selectAll("path.tm-land.is-hot").raise();
    svg.selectAll(".tm-home").raise();
  }

  const fireTimers = {};
  function flashCountry(iso) {
    if (!iso) return;
    const nodes = svg.selectAll("path.tm-land").filter(function () {
      return this.getAttribute("data-iso") === iso;
    });
    if (nodes.empty()) return;
    nodes.classed("is-firing", true).raise();
    svg.selectAll(".tm-home").raise();
    clearTimeout(fireTimers[iso]);
    fireTimers[iso] = setTimeout(() => {
      nodes.classed("is-firing", false);
    }, 900);
  }

  function cacheCentroids(features) {
    state.centroids = {};
    state.isoNames = {};
    state.nameToIso = {};
    features.forEach((d) => {
      const iso = isoOf(d);
      const name = (d.properties && d.properties.name) || "";
      if (iso && name) {
        state.isoNames[iso] = name;
        state.nameToIso[name] = iso;
      }
      if (!iso || !state.path) return;
      const c = state.path.centroid(d);
      if (c && isFinite(c[0]) && isFinite(c[1])) state.centroids[iso] = c;
    });
    pinHome();
  }

  function pinHome() {
    if (!state.projection) return;
    const xy = state.projection([HOME.lon, HOME.lat]);
    if (xy && isFinite(xy[0]) && isFinite(xy[1])) state.homeXY = xy;
  }

  function ensureHome() {
    if (state.homeXY && isFinite(state.homeXY[0])) return state.homeXY;
    pinHome();
    if (state.homeXY && isFinite(state.homeXY[0])) return state.homeXY;
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 520;
    state.homeXY = [w * 0.22, h * 0.44];
    return state.homeXY;
  }

  function jitter(xy, amt) {
    const a = amt || 10;
    return [xy[0] + (Math.random() - 0.5) * a, xy[1] + (Math.random() - 0.5) * a];
  }

  function originXY(iso) {
    iso = (iso || "XX").toUpperCase();
    if (state.centroids[iso]) return jitter(state.centroids[iso], 14);
    const ll = FALLBACK_LL[iso];
    if (ll && state.projection) {
      const xy = state.projection(ll);
      if (xy && isFinite(xy[0]) && isFinite(xy[1])) return jitter(xy, 12);
    }
    const dest = ensureHome();
    const a = Math.random() * Math.PI * 2;
    const r = 140 + Math.random() * 80;
    return [dest[0] + Math.cos(a) * r, dest[1] + Math.sin(a) * r * 0.55];
  }

  function drawHomePin() {
    svg.selectAll(".tm-home").remove();
    const dest = ensureHome();
    if (!dest) return;
    const g = svg.append("g").attr("class", "tm-home").attr("pointer-events", "none");
    g.append("circle")
      .attr("cx", dest[0])
      .attr("cy", dest[1])
      .attr("r", 7)
      .attr("fill", "#7CFF6B")
      .attr("stroke", "#041")
      .attr("stroke-width", 2);
    g.append("text")
      .attr("x", dest[0] + 10)
      .attr("y", dest[1] + 4)
      .attr("fill", "#b8ffcf")
      .attr("font-size", 11)
      .attr("font-weight", 700)
      .attr("font-family", "ui-monospace, Menlo, monospace")
      .text("▲ " + HOME.label);
  }

  async function drawWorld() {
    const { w, h } = sizeCanvas();
    const world = await (await fetch("/static/security/threat-map/data/countries-110m.json")).json();
    const features = topojson.feature(world, world.objects.countries).features;
    state.features = features;
    state.projection = d3.geoNaturalEarth1().fitExtent(
      [
        [8, 8],
        [w - 8, h - 8],
      ],
      { type: "FeatureCollection", features }
    );
    state.path = d3.geoPath(state.projection);
    cacheCentroids(features);
    svg.selectAll("*").remove();
    const defs = svg.append("defs");
    const glow = defs.append("filter").attr("id", "tm-hot-glow").attr("x", "-20%").attr("y", "-20%").attr("width", "140%").attr("height", "140%");
    glow.append("feGaussianBlur").attr("in", "SourceGraphic").attr("stdDeviation", "1.2").attr("result", "b");
    const merge = glow.append("feMerge");
    merge.append("feMergeNode").attr("in", "b");
    merge.append("feMergeNode").attr("in", "SourceGraphic");
    svg.append("rect").attr("width", w).attr("height", h).attr("fill", "transparent");
    svg
      .selectAll("path.tm-land")
      .data(features)
      .join("path")
      .attr("class", "tm-land")
      .attr("d", state.path)
      .on("click", (ev, d) => {
        ev.stopPropagation();
        const iso = isoOf(d);
        if (iso) openCountry(iso);
      });
    drawHomePin();
    paintCountries();
  }

  function fmtCount(n) {
    const v = Number(n);
    if (!isFinite(v)) return "—";
    try {
      return v.toLocaleString();
    } catch (e) {
      return String(v);
    }
  }

  function windowLabel(win) {
    const labels = { live: "Live", "24h": "24 hour", "7d": "7 day", "30d": "30 day", lifetime: "Lifetime" };
    return labels[win] || win || "Window";
  }

  function syncDrawerModes() {
    const winBtn = document.getElementById("tm-dmode-window");
    const histBtn = document.getElementById("tm-dmode-history");
    if (winBtn) {
      winBtn.textContent = windowLabel(state.window);
      winBtn.classList.toggle("is-on", state.drawerMode !== "history");
    }
    if (histBtn) histBtn.classList.toggle("is-on", state.drawerMode === "history");
  }

  function setStats(s) {
    state.summary = s || {};
    const box = document.getElementById("tm-stats");
    box.querySelector('[data-k="total"]').textContent = fmtCount(s.total);
    box.querySelector('[data-k="ips"]').textContent = fmtCount(s.unique_ips);
    box.querySelector('[data-k="ccs"]').textContent = fmtCount(s.unique_countries);
    box.querySelector('[data-k="ms"]').textContent = s.q_ms != null ? s.q_ms + " ms" : "";
    const lifeWrap = box.querySelector('[data-k="life-wrap"]');
    const lifeEl = box.querySelector('[data-k="life"]');
    const life = s.lifetime_total;
    if (lifeWrap && lifeEl && life != null && isFinite(Number(life))) {
      lifeEl.textContent = fmtCount(life);
      lifeWrap.hidden = false;
    } else if (lifeWrap) {
      lifeWrap.hidden = true;
    }
    renderBoard();
  }

  function renderBoard() {
    const s = state.summary || {};
    const sub = document.getElementById("tm-board-sub");
    if (sub) {
      const bits = [
        fmtCount(s.total ?? 0) + " events",
        fmtCount(s.unique_ips ?? 0) + " IPs",
        fmtCount(s.unique_countries ?? 0) + " countries",
      ];
      if (s.unresolved_events) bits.push(s.unresolved_events + " unmapped");
      if (s.lifetime_total != null && isFinite(Number(s.lifetime_total))) {
        bits.push(fmtCount(s.lifetime_total) + " lifetime");
      }
      if (state.window === "live") bits.unshift("Last 4 hours");
      sub.textContent = bits.join("  ·  ");
    }
    const famBox = document.getElementById("tm-board-fams");
    if (famBox) {
      famBox.innerHTML = "";
      (s.families || []).forEach((f) => {
        if (!f || !(f.count > 0)) return;
        const meta = famMeta(f.family);
        const li = document.createElement("li");
        li.innerHTML =
          '<span><span class="tm-dot" style="background:' +
          meta.color +
          '"></span>' +
          meta.label +
          "</span><strong>" +
          f.count +
          "</strong>";
        famBox.appendChild(li);
      });
      if (!famBox.children.length) famBox.innerHTML = "<li class='tm-empty'>No hits in this window</li>";
    }
    const ccBox = document.getElementById("tm-board-ccs");
    if (ccBox) {
      ccBox.innerHTML = "";
      Object.values(state.countries)
        .filter((c) => c && c.count)
        .sort((a, b) => b.count - a.count)
        .slice(0, 12)
        .forEach((c) => {
          const li = document.createElement("li");
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "tm-cc";
          btn.setAttribute("data-iso", c.iso2);
          btn.innerHTML =
            "<b>" +
            c.iso2 +
            "</b><span>" +
            countryName(c.iso2) +
            "</span><strong>" +
            fmtCount(c.count) +
            "</strong>";
          btn.addEventListener("click", () => openCountry(c.iso2));
          li.appendChild(btn);
          ccBox.appendChild(li);
        });
      if (!ccBox.children.length) ccBox.innerHTML = "<li class='tm-empty'>No mapped origins</li>";
    }
    renderRecent();
  }

  function renderRecent() {
    const rec = document.getElementById("tm-board-recent");
    if (!rec) return;
    const recH = document.getElementById("tm-board-recent-h");
    if (recH) {
      recH.textContent = state.window === "live" ? "Recent hits (last 4 hours)" : "Recent hits";
    }
    rec.innerHTML = "";
    state.recent.slice(0, 24).forEach((ev) => {
      const meta = famMeta(ev.family);
      const li = document.createElement("li");
      li.innerHTML =
        '<span class="tm-dot" style="background:' +
        meta.color +
        '"></span><b>' +
        originLabel(ev.iso2) +
        "</b><span>" +
        meta.label +
        (ev.iso2 === "XX" ? " · country unknown" : "") +
        (ev.at ? " · " + esc(ev.at) : "") +
        "</span>" +
        userBit(ev) +
        "<em>→ " +
        HOME.label +
        "</em>";
      rec.appendChild(li);
    });
    if (!rec.children.length) rec.innerHTML = "<li class='tm-empty'>No hits in this window</li>";
  }

  async function loadRecent(win) {
    try {
      const data = await fetchJSON(
        "/security/threat-map/recent?window=" + encodeURIComponent(win) + "&limit=24"
      );
      state.recent = (data.events || []).map((ev) => {
        if (ev.ip) state.seenIps[ev.ip] = true;
        return {
          id: ev.id,
          iso2: ev.iso2,
          family: ev.family,
          username: ev.username || "",
          user_id: ev.user_id || null,
          at: ev.at || "",
          live: false,
        };
      });
    } catch (e) {
      if (isAbort(e)) return;
      state.recent = [];
    }
    renderRecent();
  }

  function pickCountryAtEvent(ev) {
    if (!state.projection || !state.features.length) return;
    const node = svg.node();
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const vb = node.viewBox.baseVal;
    const x = ((ev.clientX - rect.left) / rect.width) * (vb.width || rect.width);
    const y = ((ev.clientY - rect.top) / rect.height) * (vb.height || rect.height);
    const ll = state.projection.invert([x, y]);
    if (!ll || !isFinite(ll[0])) return;
    for (let i = 0; i < state.features.length; i++) {
      const f = state.features[i];
      try {
        if (d3.geoContains(f, ll)) {
          const iso = isoOf(f);
          if (iso) openCountry(iso);
          return;
        }
      } catch (e) {
        /* skip bad geom */
      }
    }
  }

  function isAbort(err) {
    return !!(err && (err.name === "AbortError" || err.message === "aborted"));
  }

  async function fetchJSON(url) {
    const res = await fetch(url, {
      headers: { Accept: "application/json", "Cache-Control": "no-cache" },
      credentials: "same-origin",
      cache: "no-store",
      signal: pageCtl.signal,
    });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || ct.indexOf("json") === -1) throw new Error("auth");
    return res.json();
  }

  let screenLock = null;
  async function requestWakeLock() {
    if (!navigator.wakeLock || !navigator.wakeLock.request) return;
    try {
      if (screenLock) return;
      screenLock = await navigator.wakeLock.request("screen");
      screenLock.addEventListener("release", function () {
        screenLock = null;
      });
    } catch (e) {
      screenLock = null;
    }
  }
  function releaseWakeLock() {
    if (!screenLock) return;
    try {
      screenLock.release();
    } catch (e) {}
    screenLock = null;
  }

  function sleepMap() {
    state.awake = false;
    releaseWakeLock();
    try {
      pageCtl.abort();
    } catch (e) {
      /* ignore */
    }
    pageCtl = new AbortController();
    stopLive();
    stopReplay();
    if (state.livePlayTimer) {
      clearTimeout(state.livePlayTimer);
      state.livePlayTimer = null;
    }
    state.liveQueue = [];
    if (state.fxRaf) {
      cancelAnimationFrame(state.fxRaf);
      state.fxRaf = 0;
    }
  }

  function wakeMap() {
    if (state.awake) return;
    state.awake = true;
    requestWakeLock();
    tickFx();
    if (!state.booted) return;
    if (!state.summary) loadWindow(state.window);
    else armWatch();
  }

  function showBanner(ev, live, holdMs) {
    const meta = famMeta(ev.family);
    const hold = holdMs || (live ? BANNER_MS : HOLD_MS);
    banner.hidden = false;
    banner.classList.toggle("is-soft", meta.style === "trap" || meta.style === "spike");
    banner.classList.toggle("is-storm", meta.style === "storm");
    bannerTag.textContent = live ? "⚠ LIVE HIT" : "▶ REPLAY";
    bannerMsg.textContent =
      meta.label.toUpperCase() +
      "   ·   " +
      originLabel(ev.iso2) +
      "  →  " +
      HOME.label +
      userPlain(ev);
    banner.classList.toggle("has-user", !!(ev.username || ev.user_id));
    clearTimeout(bannerHide);
    bannerHide = setTimeout(function hideBanner() {
      if (live && liveDrainPending()) {
        bannerHide = setTimeout(hideBanner, BANNER_MS);
        return;
      }
      banner.hidden = true;
    }, hold);
  }

  function pushFeed(ev) {
    if (!feed) return;
    const meta = famMeta(ev.family);
    const row = document.createElement("div");
    row.className = "tm-feed-row";
    row.innerHTML =
      '<span class="tm-feed-dot" style="background:' +
      meta.color +
      '"></span><b>' +
      originLabel(ev.iso2) +
      "</b><span>" +
      meta.label +
      (ev.iso2 === "XX" ? " · country unknown" : "") +
      "</span>" +
      userBit(ev) +
      "<em>→ " +
      HOME.label +
      "</em>";
    feed.prepend(row);
    while (feed.children.length > 7) feed.removeChild(feed.lastChild);
  }

  function spawnAttack(iso, family) {
    const dest = ensureHome();
    const src = originXY(iso);
    if (!dest || !src) return;
    const meta = famMeta(family);
    const bolts = meta.style === "storm" ? 3 : 1;
    for (let i = 0; i < bolts; i++) {
      const ox = (i - (bolts - 1) / 2) * 16;
      state.fx.push({
        kind: "arc",
        x0: src[0] + ox * 0.3,
        y0: src[1] + ox * 0.15,
        x1: dest[0],
        y1: dest[1],
        t: 0,
        fade: 1,
        speed: arcStep(meta.style) * (1 + i * 0.08),
        color: meta.color,
        style: meta.style,
        family: family,
        wobble: i * 14,
      });
    }
    state.fx.push({
      kind: "ring",
      x: src[0],
      y: src[1],
      r: 4,
      a: 1,
      color: meta.color,
      grow: meta.style === "storm" ? 2.2 : 1.6,
    });
    if (state.fx.length > 220) state.fx.splice(0, state.fx.length - 220);
  }

  function quadPoint(x0, y0, x1, y1, t, wobble) {
    const lift = Math.min(120, Math.hypot(x1 - x0, y1 - y0) * 0.32) + (wobble || 0);
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2 - lift;
    const u = 1 - t;
    return [u * u * x0 + 2 * u * t * cx + t * t * x1, u * u * y0 + 2 * u * t * cy + t * t * y1];
  }

  function drawArc(p) {
    const travel = Math.min(1, p.t);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    if (p.style === "scan") ctx.setLineDash([7, 6]);
    else if (p.style === "trap") ctx.setLineDash([2, 5]);
    else ctx.setLineDash([]);

    ctx.beginPath();
    const steps = 36;
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * travel;
      const [x, y] = quadPoint(p.x0, p.y0, p.x1, p.y1, t, p.wobble);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = p.color;
    ctx.globalAlpha = 0.95 * p.fade;
    ctx.lineWidth = p.style === "storm" ? 3.4 : p.style === "strike" ? 3.1 : 2.4;
    ctx.shadowColor = p.color;
    ctx.shadowBlur = 18;
    ctx.stroke();

    ctx.lineWidth = 1.1;
    ctx.globalAlpha = 0.55 * p.fade;
    ctx.shadowBlur = 0;
    ctx.stroke();

    const head = quadPoint(p.x0, p.y0, p.x1, p.y1, travel, p.wobble);
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(head[0], head[1], p.style === "trap" ? 6 : 4.2, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.globalAlpha = p.fade;
    ctx.shadowColor = p.color;
    ctx.shadowBlur = 16;
    ctx.fill();
    ctx.restore();
  }

  function drawRadar() {
    const dest = state.homeXY;
    if (!dest) return;
    state.radarT = (state.radarT + 0.012) % 1;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < 3; i++) {
      const t = (state.radarT + i / 3) % 1;
      ctx.beginPath();
      ctx.arc(dest[0], dest[1], 6 + t * 34, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(124,255,107," + (0.55 * (1 - t)).toFixed(3) + ")";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(dest[0], dest[1], 4, 0, Math.PI * 2);
    ctx.fillStyle = "#7CFF6B";
    ctx.shadowColor = "#7CFF6B";
    ctx.shadowBlur = 12;
    ctx.fill();
    ctx.restore();
  }

  function tickFx() {
    if (!state.awake) {
      state.fxRaf = 0;
      return;
    }
    if (!canvas.width) sizeCanvas();
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 520;
    ctx.clearRect(0, 0, w, h);
    drawRadar();
    const next = [];
    state.fx.forEach((p) => {
      if (p.kind === "arc") {
        drawArc(p);
        if (p.t < 1) {
          p.t += p.speed;
          next.push(p);
        } else {
          if (!p.hit) {
            p.hit = true;
            next.push({
              kind: "impact",
              x: p.x1,
              y: p.y1,
              r: 5,
              a: 1,
              color: p.color,
              style: p.style,
            });
          }
          p.fade -= 0.08;
          if (p.fade > 0) next.push(p);
        }
      } else if (p.kind === "ring") {
        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = p.a;
        ctx.lineWidth = 2.4;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 10;
        ctx.stroke();
        ctx.restore();
        p.r += p.grow || 1;
        p.a -= 0.025;
        if (p.a > 0) next.push(p);
      } else if (p.kind === "impact") {
        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        ctx.beginPath();
        if (p.style === "trap") {
          ctx.strokeRect(p.x - p.r, p.y - p.r, p.r * 2, p.r * 2);
        } else if (p.style === "scan") {
          ctx.moveTo(p.x - p.r * 1.8, p.y);
          ctx.lineTo(p.x + p.r * 1.8, p.y);
          ctx.moveTo(p.x, p.y - p.r * 1.8);
          ctx.lineTo(p.x, p.y + p.r * 1.8);
        } else if (p.style === "strike") {
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.moveTo(p.x - p.r, p.y - p.r * 0.3);
          ctx.lineTo(p.x + p.r, p.y + p.r * 0.3);
        } else {
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        }
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = p.a;
        ctx.lineWidth = 2.6;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 14;
        ctx.stroke();
        ctx.restore();
        p.r += p.style === "storm" ? 2.6 : 1.8;
        p.a -= 0.035;
        if (p.a > 0) next.push(p);
      }
    });
    ctx.globalAlpha = 1;
    state.fx = next;
    state.fxRaf = requestAnimationFrame(tickFx);
  }

  function stopLive() {
    if (state.liveTimer) {
      clearInterval(state.liveTimer);
      state.liveTimer = null;
    }
  }

  function clearReplayTimer() {
    if (state.replayTimer) {
      clearInterval(state.replayTimer);
      state.replayTimer = null;
    }
  }

  function stopReplay() {
    clearReplayTimer();
    state.playing = false;
    playBtn.classList.remove("is-on");
    playBtn.textContent = state.window === "live" ? "LIVE" : "Play replay";
  }

  function setArmPlate(mode, sub, link) {
    if (!armBox) return;
    const words = {
      standby: "STANDBY",
      arming: "ARMING",
      armed: "ARMED",
      contact: "CONTACT",
      offline: "NO LINK",
    };
    armBox.dataset.state = mode;
    if (armWord) armWord.textContent = words[mode] || "ARMED";
    if (armSub && sub) armSub.textContent = String(sub).toUpperCase();
    if (armLink && link) armLink.textContent = String(link).toUpperCase();
    if (playStatus) {
      playStatus.classList.toggle("is-armed", mode === "armed" || mode === "contact");
    }
  }

  function restoreArmedSoon(holdMs) {
    clearTimeout(armHold);
    const wait = holdMs || BANNER_MS;
    armHold = setTimeout(() => {
      if (!state.armed) return;
      if (liveDrainPending()) {
        restoreArmedSoon(BANNER_MS);
        return;
      }
      const last = state.pulse ? ageLabel(state.pulse.last_event_age_sec) : "";
      setArmPlate(
        "armed",
        last ? "LAST CONTACT " + last : "GRID LOCKED · TRACKING",
        "LINK LIVE"
      );
      if (state.window === "live" && playStatus) {
        playStatus.textContent = last ? "LIVE  //  ARMED  ·  " + last : "LIVE  //  ARMED";
      }
    }, wait);
  }

  function markContact(detail, holdMs) {
    setArmPlate("contact", detail || "INBOUND THREAT", "LOCK");
    if (playStatus) playStatus.textContent = "LIVE  //  CONTACT";
    playContactAlert();
    restoreArmedSoon(holdMs);
  }

  function applyLiveHit(ev) {
    if (!state.summary) {
      state.summary = { total: 0, unique_ips: 0, unique_countries: 0, families: [] };
    }
    const iso = (ev.iso2 || "XX").toUpperCase();
    const fam = ev.family || "other";
    let row = state.countries[iso];
    if (!row) {
      row = { iso2: iso, count: 0, unique_ips: 0, families: {}, top_family: fam };
      state.countries[iso] = row;
    }
    row.count = (row.count || 0) + 1;
    row.families = row.families || {};
    row.families[fam] = (row.families[fam] || 0) + 1;
    if (!row.top_family || row.families[fam] >= (row.families[row.top_family] || 0)) {
      row.top_family = fam;
    }
    const ip = (ev.ip || "").trim();
    if (ip && !state.seenIps[ip]) {
      state.seenIps[ip] = true;
      row.unique_ips = (row.unique_ips || 0) + 1;
      state.summary.unique_ips = (state.summary.unique_ips || 0) + 1;
    }
    state.summary.total = (state.summary.total || 0) + 1;
    const fams = Array.isArray(state.summary.families) ? state.summary.families.slice() : [];
    let hit = null;
    for (let i = 0; i < fams.length; i++) {
      if (fams[i] && fams[i].family === fam) {
        hit = fams[i];
        break;
      }
    }
    if (!hit) {
      hit = { family: fam, count: 0 };
      fams.push(hit);
    }
    hit.count += 1;
    fams.sort((a, b) => (b.count || 0) - (a.count || 0));
    state.summary.families = fams;
    state.summary.unique_countries = Object.values(state.countries).filter((c) => c && c.count).length;
    paintCountries();
    setStats(state.summary);
  }

  function fireEvent(ev, live, opts) {
    const holdMs = (opts && opts.holdMs) || (live ? BANNER_MS : HOLD_MS);
    spawnAttack(ev.iso2, ev.family);
    flashCountry(ev.iso2);
    showBanner(ev, !!live, holdMs);
    if (live) {
      const meta = famMeta(ev.family);
      markContact(meta.label + "  " + originLabel(ev.iso2) + " → " + HOME.label + userPlain(ev), holdMs);
    }
    if (live) applyLiveHit(ev);
    pushFeed(ev);
    const row = {
      id: ev.id || 0,
      iso2: ev.iso2,
      family: ev.family,
      username: ev.username || "",
      user_id: ev.user_id || null,
      at: ev.at || "",
      live: !!live,
    };
    if (row.id) {
      state.recent = state.recent.filter((x) => x.id !== row.id);
    }
    state.recent.unshift(row);
    if (state.recent.length > 24) state.recent.length = 24;
    renderRecent();
  }

  function intervalMs() {
    // 1x is the watchable pace (one bolt, one banner). Higher is faster, still visible.
    const pace = { 1: 2200, 2: 1300, 4: 750, 8: 420, 16: 220, 32: 110 };
    const s = Number(state.speed) || 1;
    if (pace[s]) return pace[s];
    return Math.max(90, Math.round(2200 / s));
  }

  function arcStep(style) {
    const base = style === "storm" ? 0.01 : style === "scan" ? 0.008 : 0.011;
    const s = Math.max(1, Number(state.speed) || 1);
    return base * (1 + (s - 1) * 0.18);
  }

  function ageLabel(sec) {
    if (sec == null || !isFinite(sec)) return "";
    const n = Math.max(0, Math.round(sec));
    if (n < 10) return "just now";
    if (n < 60) return n + "s ago";
    if (n < 3600) return Math.round(n / 60) + "m ago";
    if (n < 86400) return Math.round(n / 3600) + "h ago";
    return Math.round(n / 86400) + "d ago";
  }

  function renderPulse() {
    const el = document.getElementById("tm-pulse");
    if (!el) return;
    const p = state.pulse;
    if (!p) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.classList.toggle("is-live", !!state.lastPollOk);
    el.classList.toggle("is-quiet", !!state.lastPollOk && !(p.events_15m > 0));
    const last = ageLabel(p.last_event_age_sec);
    const bits = [];
    if (state.lastPollOk) bits.push(p.events_15m > 0 ? "live feed" : "live · quiet");
    else bits.push("feed error");
    if (last) bits.push("last hit " + last);
    bits.push((p.events_15m || 0) + " in 15m");
    bits.push((p.events_1h || 0) + " in 1h");
    bits.push((p.events_24h || 0) + " in 24h");
    el.textContent = bits.join("  ·  ");
  }

  function applyPulse(data) {
    if (!data) return;
    state.pulse = {
      events_15m: data.events_15m || 0,
      events_1h: data.events_1h || 0,
      events_24h: data.events_24h || 0,
      last_event_at: data.last_event_at || null,
      last_event_age_sec: data.last_event_age_sec,
    };
    renderPulse();
  }

  function enqueueLive(events) {
    if (!events || !events.length) return;
    state.liveQueue.push.apply(state.liveQueue, events);
    drainLive();
  }

  function drainLive() {
    if (state.livePlayTimer) return;
    const tick = () => {
      state.livePlayTimer = null;
      if (!state.liveQueue.length) return;
      const more = state.liveQueue.length > 1;
      const ev = state.liveQueue.shift();
      const holdMs = more ? BANNER_MS : BANNER_LAST_MS;
      fireEvent(ev, true, { holdMs: holdMs });
      if (state.window === "live" || !state.playing) {
        playStatus.textContent =
          "LIVE  //  CONTACT" +
          (state.liveQueue.length ? "  ·  " + state.liveQueue.length + " IN QUEUE" : "");
      }
      if (!state.liveQueue.length) {
        state.livePlayTimer = null;
        return;
      }
      state.livePlayTimer = setTimeout(tick, BANNER_MS);
    };
    tick();
  }

  function startReplay(auto, fromStart, onDone) {
    clearReplayTimer();
    if (!state.replay.length) {
      playStatus.textContent = "nothing to replay";
      playBtn.textContent = "Play replay";
      playBtn.classList.remove("is-on");
      if (onDone) onDone();
      return;
    }
    if (fromStart || state.replayI >= state.replay.length) state.replayI = 0;
    state.playing = true;
    state.onReplayDone = onDone || null;
    playBtn.classList.add("is-on");
    playBtn.textContent = "Pause";
    const n = state.replay.length;
    playStatus.textContent = (auto ? "auto " : "") + state.replayI + " / " + n + "  ·  " + state.speed + "x";
    state.replayTimer = setInterval(() => {
      if (document.hidden) return;
      if (state.replayI >= state.replay.length) {
        const done = state.onReplayDone;
        stopReplay();
        playStatus.textContent = n + " / " + n + " done";
        if (done) done();
        return;
      }
      const ev = state.replay[state.replayI++];
      fireEvent(ev, false);
      playStatus.textContent = state.replayI + " / " + n + "  ·  " + state.speed + "x";
    }, intervalMs());
  }

  function setLoad(on, title, msg, pct) {
    const box = document.getElementById("tm-load");
    if (!box) return;
    box.hidden = !on;
    const t = document.getElementById("tm-load-title");
    const m = document.getElementById("tm-load-msg");
    const fill = document.getElementById("tm-load-fill");
    if (t && title) t.textContent = title;
    if (m && msg != null) m.textContent = msg;
    if (fill) {
      const n = Math.max(0, Math.min(100, Number(pct) || 0));
      fill.style.width = n + "%";
    }
  }

  async function loadReplay(win) {
    const big = win === "lifetime" || win === "30d";
    playStatus.textContent = "loading replay…";
    setLoad(
      true,
      big ? "Loading large replay" : "Loading replay",
      win === "lifetime" ? "All-time history — this can take a while" : "Fetching hits…",
      2
    );
    try {
      const all = [];
      let after = 0;
      let total = 0;
      let guard = 0;
      while (guard < 400) {
        guard += 1;
        const data = await fetchJSON(
          "/security/threat-map/replay?window=" +
            encodeURIComponent(win) +
            "&limit=1500&after_id=" +
            after
        );
        const batch = data.events || [];
        total = Number(data.total) || total || batch.length;
        all.push.apply(all, batch);
        state.replay = all;
        const pct = total > 0 ? Math.min(99, Math.round((all.length / total) * 100)) : 8;
        setLoad(
          true,
          big ? "Loading large replay" : "Loading replay",
          fmtCount(all.length) + " / " + fmtCount(total || all.length) + " hits",
          pct
        );
        playStatus.textContent = "loading  " + all.length + " / " + (total || "…");
        if (all.length && !state.playing && state.window === win && win !== "live") {
          startReplay(true, true);
        }
        if (!data.has_more || !batch.length) break;
        after = Number(data.next_after_id) || after;
        if (!after) break;
      }
      state.replay = all;
      if (!state.playing) state.replayI = 0;
      playStatus.textContent = fmtCount(all.length) + " hits queued";
      setLoad(false);
      return all.length;
    } catch (e) {
      setLoad(false);
      if (isAbort(e)) return 0;
      playStatus.textContent = e.message === "auth" ? "session expired" : "replay failed";
      state.replay = [];
      return 0;
    }
  }

  function armWatch() {
    if (state.liveTimer) return;
    const poll = async () => {
      if (document.hidden || !state.awake || state.pollInFlight) return;
      state.pollInFlight = true;
      try {
        const data = await fetchJSON(
          "/security/threat-map/live?since_id=" +
            state.sinceId +
            "&limit=80" +
            (state.armed ? "&follow=1" : "&arm=1") +
            "&_=" +
            Date.now()
        );
        state.lastPollOk = true;
        state.authFails = 0;
        applyPulse(data);
        const events = data.events || [];
        const maxId = Number(data.max_id);
        if (!state.armed) {
          state.sinceId = isFinite(maxId) ? maxId : 0;
          state.armed = true;
          const last = ageLabel(data.last_event_age_sec);
          setArmPlate(
            "armed",
            last ? "LAST CONTACT " + last : "GRID LOCKED · TRACKING",
            "LINK LIVE"
          );
          if (state.window === "live") playStatus.textContent = "LIVE  //  ARMED";
          return;
        }
        if (isFinite(maxId) && maxId >= state.sinceId) state.sinceId = maxId;
        enqueueLive(events);
        if (armBox && armBox.dataset.state !== "contact") {
          const last = ageLabel(data.last_event_age_sec);
          setArmPlate(
            state.lastPollOk ? "armed" : "offline",
            last ? "LAST CONTACT " + last : "GRID LOCKED · TRACKING",
            state.lastPollOk ? "LINK LIVE" : "DOWN"
          );
        }
        if (events.length && (state.window === "live" || !state.playing)) {
          playStatus.textContent =
            (state.window === "live" ? "LIVE  //  CONTACT" : "WATCHING") +
            "  +" +
            events.length +
            (state.liveQueue.length ? "  ·  " + state.liveQueue.length + " IN QUEUE" : "");
        } else if (state.window === "live" && !state.playing && !state.liveQueue.length) {
          const last = ageLabel(data.last_event_age_sec);
          playStatus.textContent = last ? "LIVE  //  ARMED  ·  " + last : "LIVE  //  ARMED";
        }
      } catch (e) {
        if (isAbort(e) || !state.awake) return;
        state.lastPollOk = false;
        renderPulse();
        setArmPlate("offline", "FEED ERROR", "DOWN");
        if (e.message === "auth") {
          state.authFails = (state.authFails || 0) + 1;
          if (state.authFails >= 4) stopLive();
        }
      } finally {
        state.pollInFlight = false;
      }
    };
    poll();
    state.liveTimer = setInterval(poll, 2500);
  }

  function startLive() {
    stopReplay();
    state.replay = [];
    state.replayI = 0;
    playBtn.textContent = "LIVE";
    playBtn.classList.add("is-on");
    playStatus.textContent = "LIVE  //  ARMING";
    setArmPlate("arming", "ACQUIRING GRID", "SYNC");
    setLoad(false);
    armWatch();
  }

  async function loadWindow(win) {
    if (!state.awake) return;
    state.window = win;
    document.querySelectorAll(".tm-win").forEach((b) => {
      b.classList.toggle("is-on", b.getAttribute("data-window") === win);
    });
    stopLive();
    stopReplay();
    if (!state.armed) setArmPlate("standby", "SENSORS COLD", "NO LINK");
    if (state.livePlayTimer) {
      clearTimeout(state.livePlayTimer);
      state.livePlayTimer = null;
    }
    state.liveQueue = [];
    state.replay = [];
    state.replayI = 0;
    state.recent = [];
    state.seenIps = {};
    renderRecent();
    setLoad(
      true,
      win === "lifetime" ? "Loading lifetime map" : "Loading map",
      win === "lifetime" ? "All-time totals — large query" : "Fetching " + windowLabel(win) + "…",
      6
    );
    try {
      const countries = await fetchJSON("/security/threat-map/countries?window=" + encodeURIComponent(win));
      state.countries = {};
      (countries.countries || []).forEach((c) => {
        state.countries[c.iso2] = c;
      });
      paintCountries();
      const summary = await fetchJSON("/security/threat-map/summary?window=" + encodeURIComponent(win));
      setStats(summary);
      if (summary.recent && summary.recent.length) {
        state.recent = summary.recent.map((ev) => {
          if (ev.ip) state.seenIps[ev.ip] = true;
          return {
            id: ev.id,
            iso2: ev.iso2,
            family: ev.family,
            username: ev.username || "",
            user_id: ev.user_id || null,
            at: ev.at || "",
            live: false,
          };
        });
        renderRecent();
      } else {
        await loadRecent(win);
      }
      const hud = document.getElementById("tm-hud");
      if (countries.unresolved_ips) {
        hud.hidden = false;
        hud.textContent = countries.unresolved_ips + " IPs not yet mapped";
      } else {
        hud.hidden = true;
      }
    } catch (e) {
      setLoad(false);
      if (isAbort(e)) return;
      document.getElementById("tm-hud").hidden = false;
      document.getElementById("tm-hud").textContent =
        e.message === "auth" ? "Session expired — reload." : "Could not load threats.";
    }
    armWatch();
    if (state.selected && drawer && !drawer.hidden) {
      openCountry(state.selected, { keepMode: true });
    }
    if (win === "live") {
      startLive();
      return;
    }
    const n = await loadReplay(win);
    if (n) startReplay(true, true);
  }

  async function openCountry(iso, opts) {
    if (!state.awake) return;
    const keepMode = opts && opts.keepMode;
    if (!keepMode) state.drawerMode = "window";
    state.selected = iso;
    syncDrawerModes();
    svg.selectAll("path.tm-land").classed("is-sel", function () {
      return this.getAttribute("data-iso") === iso;
    });
    drawer.hidden = false;
    document.getElementById("tm-drawer-title").textContent =
      countryName(iso) + (iso && iso.length === 2 && iso !== "XX" && iso !== "ZZ" ? "  ·  " + iso : "");
    document.getElementById("tm-drawer-sub").textContent = "Loading…";
    document.getElementById("tm-drawer-fams").innerHTML = "";
    document.getElementById("tm-drawer-ips").innerHTML = "";
    const pulseEl = document.getElementById("tm-drawer-pulse");
    if (pulseEl) {
      pulseEl.hidden = true;
      pulseEl.textContent = "";
    }
    const usersEl0 = document.getElementById("tm-drawer-users");
    if (usersEl0) usersEl0.innerHTML = "";
    const reqWin = state.drawerMode === "history" ? "lifetime" : state.window;
    const reqMode = state.drawerMode;
    try {
      const d = await fetchJSON(
        "/security/threat-map/country/" +
          encodeURIComponent(iso) +
          "?window=" +
          encodeURIComponent(reqWin)
      );
      if (state.selected !== iso || state.drawerMode !== reqMode) return;
      const history = state.drawerMode === "history";
      const modeLabel = history ? "Lifetime" : windowLabel(state.window);
      let sub = modeLabel + " · " + fmtCount(d.count || 0) + " events";
      if (!history) sub += " · " + fmtCount(d.unique_ips || 0) + " IPs";
      if (history && d.first_seen) sub += " · since " + d.first_seen;
      if (d.last_seen) sub += " · last " + d.last_seen;
      if (iso === "XX") {
        sub += " · IPs are logged; country not in the free IPv4 table (often IPv6)";
      } else if (iso === "ZZ") {
        sub += " · private / local address, not a public country";
      }
      document.getElementById("tm-drawer-sub").textContent = sub;
      const famH = document.getElementById("tm-drawer-fam-h");
      if (famH) famH.textContent = history ? "Lifetime totals" : "Attack types";
      const ipsH = document.getElementById("tm-drawer-ips-h");
      if (ipsH) ipsH.textContent = history ? "Recent IPs" : "Sample sources";
      const usersWrap = document.getElementById("tm-drawer-users-wrap");
      if (usersWrap) usersWrap.hidden = history;
      if (pulseEl && !history && state.window === "live") {
        const bits = [];
        if (d.events_15m > 0) bits.push(fmtCount(d.events_15m) + " in 15m");
        if (d.events_1h > 0) bits.push(fmtCount(d.events_1h) + " in 1h");
        if (d.events_24h > 0) bits.push(fmtCount(d.events_24h) + " in 24h");
        if (bits.length) {
          pulseEl.hidden = false;
          pulseEl.textContent = "Recently · " + bits.join("  ·  ");
        }
      }
      const fams = document.getElementById("tm-drawer-fams");
      fams.classList.toggle("is-history", history);
      (d.families || []).forEach((f) => {
        if (!f || !(f.count > 0)) return;
        const meta = famMeta(f.family);
        const li = document.createElement("li");
        const name = history ? "Total " + meta.label : meta.label;
        li.innerHTML =
          '<span><span class="tm-dot" style="background:' +
          meta.color +
          '"></span>' +
          esc(name) +
          "</span><strong>" +
          fmtCount(f.count) +
          "</strong>";
        fams.appendChild(li);
      });
      if (!fams.children.length) {
        fams.innerHTML =
          history
            ? "<li class='tm-empty'>No lifetime hits from this origin</li>"
            : "<li class='tm-empty'>No hits in this window — try History</li>";
      }
      const usersEl = document.getElementById("tm-drawer-users");
      if (usersEl && !history) {
        (d.users || []).forEach((u) => {
          if (!u.username && !u.user_id) return;
          const li = document.createElement("li");
          li.innerHTML = '<i class="tm-user">' + esc(userLabel(u)) + "</i>";
          usersEl.appendChild(li);
        });
        if (!usersEl.children.length) {
          usersEl.innerHTML = '<li class="tm-empty">No registered account on these hits</li>';
        }
      }
      const ips = document.getElementById("tm-drawer-ips");
      (d.sample_ips || []).forEach((s) => {
        const li = document.createElement("li");
        li.innerHTML =
          "<code>" +
          esc(s.ip || "") +
          "</code><span>" +
          esc(famMeta(s.family).label) +
          "</span>" +
          userBit(s) +
          (s.at && history ? '<em class="tm-dim">' + esc(s.at) + "</em>" : "");
        ips.appendChild(li);
      });
      if (!ips.children.length) {
        ips.innerHTML = "<li class='tm-empty'>No recent IPs</li>";
      }
    } catch (e) {
      if (isAbort(e)) return;
      document.getElementById("tm-drawer-sub").textContent = "Could not load country.";
    }
  }

  document.querySelectorAll(".tm-win").forEach((btn) => {
    btn.addEventListener("click", () => loadWindow(btn.getAttribute("data-window")));
  });
  document.querySelectorAll(".tm-dmode").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-mode") === "history" ? "history" : "window";
      if (state.drawerMode === mode && state.selected) return;
      state.drawerMode = mode;
      syncDrawerModes();
      if (state.selected) openCountry(state.selected, { keepMode: true });
    });
  });
  document.querySelectorAll(".tm-speed").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.speed = Number(btn.getAttribute("data-speed")) || 1;
      document.querySelectorAll(".tm-speed").forEach((b) => {
        b.classList.toggle("is-on", b === btn);
      });
      if (state.playing) startReplay(state.window === "live", false);
    });
  });
  playBtn.addEventListener("click", () => {
    if (state.window === "live") return;
    if (state.playing) {
      stopReplay();
      playStatus.textContent = "paused " + state.replayI + " / " + state.replay.length;
      return;
    }
    if (!state.replay.length) {
      loadReplay(state.window).then((n) => {
        if (n) startReplay(false, true);
      });
      return;
    }
    startReplay(false, state.replayI >= state.replay.length);
  });
  document.querySelector(".tm-stage").addEventListener("click", (ev) => {
    if (ev.target.closest(".tm-drawer, .tm-land, button, a")) return;
    pickCountryAtEvent(ev);
  });
  document.getElementById("tm-close").addEventListener("click", () => {
    drawer.hidden = true;
    state.selected = null;
    state.drawerMode = "window";
    syncDrawerModes();
    svg.selectAll("path.tm-land").classed("is-sel", false);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) sleepMap();
    else wakeMap();
  });
  window.addEventListener("pagehide", sleepMap);
  window.addEventListener("pageshow", () => {
    if (!state.awake) wakeMap();
  });
  window.addEventListener("resize", () => {
    if (!state.awake || !state.booted) return;
    drawWorld().then(paintCountries);
  });

  function startMap() {
    if (state.booted) {
      wakeMap();
      return;
    }
    state.booted = true;
    state.awake = true;
    requestWakeLock();
    tickFx();
    loadIso()
      .then(drawWorld)
      .then(() => {
        if (!state.awake) return;
        return loadWindow("live");
      })
      .catch((e) => {
        if (isAbort(e)) return;
        const hud = document.getElementById("tm-hud");
        hud.hidden = false;
        hud.textContent = "Map assets failed to load.";
      });
  }

  if (document.hidden) {
    document.addEventListener("visibilitychange", function waitVisible() {
      if (document.hidden) return;
      document.removeEventListener("visibilitychange", waitVisible);
      startMap();
    });
  } else {
    startMap();
  }
})();
