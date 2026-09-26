(function () {
  const intro = document.getElementById("apt-intro");
  const introVideo = document.getElementById("apt-intro-video");
  const introSkip = document.getElementById("apt-intro-skip");
  if (intro && introVideo && introSkip) {
    let seen = false;
    try { seen = localStorage.getItem("apt-intro-hwy") === "1"; } catch (err) { seen = true; }
    function closeIntro() {
      intro.hidden = true;
      document.body.classList.remove("intro-on");
      introVideo.pause();
      try { localStorage.setItem("apt-intro-hwy", "1"); } catch (err) {}
    }
    if (!seen) {
      intro.hidden = false;
      document.body.classList.add("intro-on");
      const giveUp = window.setTimeout(closeIntro, 9000);
      introVideo.addEventListener("ended", function () {
        window.clearTimeout(giveUp);
        closeIntro();
      });
      introSkip.addEventListener("click", function () {
        window.clearTimeout(giveUp);
        closeIntro();
      });
      introVideo.play().catch(function () {
        window.clearTimeout(giveUp);
        window.setTimeout(closeIntro, 1600);
      });
    }
  }

  const token = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  document.querySelectorAll("[data-place-search]").forEach(function (root) {
    const input = root.querySelector("[data-search]");
    const results = root.querySelector("[data-results]");
    const many = root.getAttribute("data-mode") === "many";
    const picked = root.querySelector("[data-picked]");
    const hidden = root.querySelector("[data-property-id]");
    const chosen = root.querySelector("[data-chosen]");
    if (!input || !results) return;
    let timer = null;
    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) {
        results.innerHTML = "";
        return;
      }
      timer = window.setTimeout(function () {
        fetch("/api/places?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
          .then(function (resp) { return resp.json(); })
          .then(function (rows) {
            results.innerHTML = "";
            if (!rows.length) {
              results.innerHTML = "<p class=\"lead\">Not on your sites yet. In chat: add " + q + " from the city to my sites.</p>";
              return;
            }
            rows.forEach(function (row) {
              const button = document.createElement("button");
              button.type = "button";
              button.innerHTML = row.name + "<small>" + (row.city || "") + "</small>";
              button.addEventListener("click", function () {
                if (many && picked) {
                  if (picked.querySelector("[data-id='" + row.id + "']")) return;
                  const block = document.createElement("div");
                  block.className = "job";
                  block.setAttribute("data-id", String(row.id));
                  block.innerHTML = "<strong></strong><input type=\"hidden\" name=\"property_id\"><label>Jobs here, one per line<textarea name=\"work\" placeholder=\"worked on the AC at unit 12&#10;fix the tub clog at unit 26\"></textarea></label><button type=\"button\" class=\"ghost\">Remove</button>";
                  block.querySelector("strong").textContent = row.name + (row.city ? " · " + row.city : "");
                  block.querySelector("input").value = row.id;
                  block.querySelector("button").addEventListener("click", function () { block.remove(); });
                  picked.appendChild(block);
                } else if (hidden) {
                  hidden.value = row.id;
                  if (chosen) {
                    chosen.hidden = false;
                    chosen.textContent = row.name + (row.city ? " · " + row.city : "");
                  }
                  const nameBox = root.querySelector("[data-name]");
                  const cityBox = root.querySelector("[data-city]");
                  if (nameBox) nameBox.value = row.name || "";
                  if (cityBox) cityBox.value = row.city || "";
                  const extra = root.querySelector("[data-new-place]");
                  if (extra) extra.hidden = true;
                }
                results.innerHTML = "";
                input.value = row.name || "";
              });
              results.appendChild(button);
            });
          });
      }, 200);
    });
  });

  document.querySelectorAll("[data-unit-find]").forEach(function (input) {
    const list = document.getElementById("unit-list");
    const empty = document.querySelector("[data-unit-empty]");
    if (!list) return;
    input.addEventListener("input", function () {
      const q = input.value.trim().toLowerCase();
      let shown = 0;
      list.querySelectorAll("[data-unit-card]").forEach(function (card) {
        const blob = (card.getAttribute("data-number") || "") + " " + (card.getAttribute("data-building") || "") + " " + ((card.querySelector(".unit-search-data") || {}).textContent || "");
        const hit = !q || blob.toLowerCase().indexOf(q) !== -1;
        card.hidden = !hit;
        if (hit) shown += 1;
      });
      if (empty) empty.hidden = shown !== 0;
    });
  });

  const mic = document.getElementById("mic");
  const composer = document.getElementById("composer");
  if (mic && composer && (window.SpeechRecognition || window.webkitSpeechRecognition)) {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new Rec();
    rec.onresult = function (event) {
      const said = event.results[0][0].transcript;
      const box = composer.querySelector("textarea");
      box.value = (box.value ? box.value + " " : "") + said;
    };
    mic.addEventListener("click", function () {
      try { rec.start(); } catch (err) { /* already listening */ }
    });
  }

  function drafts() {
    try { return JSON.parse(localStorage.getItem("apt-drafts") || "[]"); }
    catch (err) { return []; }
  }
  function saveDrafts(rows) {
    localStorage.setItem("apt-drafts", JSON.stringify(rows));
  }
  const moneyTalk = /filled up|\$\s*\d|yes,\s*save it|^save it$|^save$/i;

  const panel = document.getElementById("chat-panel");
  const openChat = document.getElementById("chat-open");
  const closeChat = document.getElementById("chat-close");
  const newChat = document.getElementById("chat-new");
  function setChat(open) {
    if (!panel || !openChat) return;
    panel.hidden = !open;
    panel.classList.toggle("is-open", !!open);
    openChat.hidden = !!open;
    openChat.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const box = panel.querySelector("textarea");
      if (box) box.focus();
      const thread = document.getElementById("thread");
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  }
  if (openChat) openChat.addEventListener("click", function () { setChat(true); });
  if (closeChat) closeChat.addEventListener("click", function () { setChat(false); });
  if (newChat) {
    newChat.addEventListener("click", function () {
      fetch("/chat/new", {
        method: "POST",
        headers: { "X-CSRF-Token": token, Accept: "application/json" }
      }).catch(function () {});
      const thread = document.getElementById("thread");
      if (thread) {
        thread.innerHTML = "";
        addBubble("assistant", "New chat. Tell me what you're doing.");
      }
    });
  }
  setChat(false);

  function addBubble(role, text) {
    const thread = document.getElementById("thread");
    if (!thread || !text) return;
    const bubble = document.createElement("p");
    bubble.className = "bubble " + role;
    if (role === "assistant") {
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = (document.getElementById("thread") || {}).getAttribute("data-assistant") || "Apt";
      bubble.appendChild(who);
    }
    bubble.appendChild(document.createTextNode(text));
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    bubble.appendChild(time);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  const box = composer ? composer.querySelector("textarea") : null;
  const photo = composer ? composer.querySelector('input[name="photo"]') : null;
  const photoLabel = composer ? composer.querySelector(".clip span") : null;
  if (box) {
    box.addEventListener("input", function () {
      box.style.height = "auto";
      box.style.height = Math.min(box.scrollHeight, 140) + "px";
    });
    box.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (composer.requestSubmit) composer.requestSubmit();
        else composer.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    });
  }
  if (photo && photoLabel) {
    photo.addEventListener("change", function () {
      const file = photo.files && photo.files[0];
      photoLabel.textContent = file ? file.name : "Photo";
    });
  }
  const openThread = document.getElementById("thread");
  if (openThread) openThread.scrollTop = openThread.scrollHeight;

  if (composer) {
    composer.addEventListener("submit", function (event) {
      const text = (composer.querySelector("textarea").value || "").trim();
      const file = photo && photo.files && photo.files[0];
      event.preventDefault();
      if (!text && !file) return;
      if (!navigator.onLine) {
        if (file || moneyTalk.test(text)) {
          addBubble("assistant", "Photos and money wait until you have signal, so nothing is filed twice.");
          return;
        }
        const rows = drafts();
        rows.push({
          message: text,
          idempotency_key: (composer.querySelector('[name="idempotency_key"]') || {}).value || String(Date.now())
        });
        saveDrafts(rows);
        composer.querySelector("textarea").value = "";
        addBubble("user", text);
        addBubble("assistant", "I'll send that when you're back online.");
        return;
      }
      const body = new FormData(composer);
      addBubble("user", text || "Photo");
      composer.querySelector("textarea").value = "";
      if (box) box.style.height = "auto";
      if (photo) photo.value = "";
      if (photoLabel) photoLabel.textContent = "Photo";
      fetch("/chat", {
        method: "POST",
        body: body,
        headers: { Accept: "application/json", "X-CSRF-Token": token }
      }).then(function (resp) { return resp.json(); }).then(function (data) {
        addBubble("assistant", (data && data.reply) || "Got it.");
        const key = composer.querySelector('[name="idempotency_key"]');
        if (key) key.value = Math.random().toString(16).slice(2) + Date.now().toString(16);
      }).catch(function () {
        addBubble("assistant", "That didn't send. Try again.");
      });
    });
  }

  async function flush() {
    if (!navigator.onLine) return;
    const rows = drafts();
    if (!rows.length || !token) return;
    const left = [];
    for (const row of rows) {
      const body = new FormData();
      body.set("csrf_token", token);
      body.set("message", row.message);
      body.set("idempotency_key", row.idempotency_key);
      try {
        const resp = await fetch("/chat", { method: "POST", body: body, headers: { "X-CSRF-Token": token, Accept: "application/json" } });
        if (!resp.ok) left.push(row);
      } catch (err) {
        left.push(row);
      }
    }
    saveDrafts(left);
  }
  window.addEventListener("online", flush);
  flush();

  const sharing = document.body.getAttribute("data-share") === "1";
  async function ping() {
    if (!sharing || !navigator.onLine || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(function (pos) {
      fetch("/api/ping", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token, Accept: "application/json" },
        body: JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude })
      });
    });
  }
  if (sharing) {
    ping();
    window.setInterval(ping, 5 * 60 * 1000);
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  const install = document.getElementById("apt-install");
  const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
  if (standalone) localStorage.setItem("apt-installed", "1");
  window.addEventListener("appinstalled", function () {
    localStorage.setItem("apt-installed", "1");
    if (install) install.hidden = true;
  });
  function alreadyInstalled() {
    return localStorage.getItem("apt-installed") === "1" || standalone;
  }
  let installEvent = null;
  window.addEventListener("beforeinstallprompt", function (event) {
    if (alreadyInstalled()) return;
    event.preventDefault();
    installEvent = event;
    if (install && localStorage.getItem("apt-install-hide") !== "1") install.hidden = false;
  });
  if (install && !alreadyInstalled() && localStorage.getItem("apt-install-hide") !== "1") {
    const ios = /iphone|ipad|ipod/i.test(window.navigator.userAgent);
    if (ios) {
      install.querySelector("p").textContent = "Install Apt: tap Share, then Add to Home Screen.";
      const go = document.getElementById("apt-install-go");
      if (go) go.hidden = true;
      install.hidden = false;
    }
    if (navigator.getInstalledRelatedApps) {
      navigator.getInstalledRelatedApps().then(function (apps) {
        if (apps && apps.length) {
          localStorage.setItem("apt-installed", "1");
          install.hidden = true;
        }
      }).catch(function () {});
    }
  }
  const installGo = document.getElementById("apt-install-go");
  const installSkip = document.getElementById("apt-install-skip");
  if (installGo) {
    installGo.addEventListener("click", function () {
      if (!installEvent) return;
      installEvent.prompt();
      installEvent.userChoice.then(function (choice) {
        if (choice && choice.outcome === "accepted") localStorage.setItem("apt-installed", "1");
        install.hidden = true;
        installEvent = null;
      });
    });
  }
  if (installSkip) {
    installSkip.addEventListener("click", function () {
      localStorage.setItem("apt-install-hide", "1");
      install.hidden = true;
    });
  }
})();
