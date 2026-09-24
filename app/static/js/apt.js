(function () {
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
                  block.innerHTML = "<strong></strong><input type=\"hidden\" name=\"property_id\"><label>Job here<textarea name=\"work\" placeholder=\"One job per line\"></textarea></label><button type=\"button\" class=\"ghost\">Remove</button>";
                  block.querySelector("strong").textContent = row.name + (row.city ? " · " + row.city : "");
                  block.querySelector("input").value = row.id;
                  block.querySelector("button").addEventListener("click", function () { block.remove(); });
                  picked.appendChild(block);
                } else if (hidden) {
                  hidden.value = row.id;
                  if (chosen) chosen.textContent = row.name + (row.city ? " · " + row.city : "");
                }
                results.innerHTML = "";
                input.value = "";
              });
              results.appendChild(button);
            });
          });
      }, 200);
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
  function setChat(open) {
    if (!panel || !openChat) return;
    panel.hidden = !open;
    openChat.classList.toggle("is-open", open);
    if (open) {
      const box = panel.querySelector("textarea");
      if (box) box.focus();
      const thread = document.getElementById("thread");
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  }
  if (openChat) openChat.addEventListener("click", function () { setChat(true); });
  if (closeChat) closeChat.addEventListener("click", function () { setChat(false); });

  function addBubble(role, text) {
    const thread = document.getElementById("thread");
    if (!thread || !text) return;
    const bubble = document.createElement("p");
    bubble.className = "bubble " + role;
    bubble.textContent = text;
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  if (composer) {
    composer.addEventListener("submit", function (event) {
      const text = (composer.querySelector("textarea").value || "").trim();
      event.preventDefault();
      if (!text) return;
      if (!navigator.onLine) {
        if (moneyTalk.test(text)) {
          window.alert("Money and “save it” wait until you have signal, so a receipt is not filed twice.");
          return;
        }
        const rows = drafts();
        rows.push({
          message: text,
          idempotency_key: (composer.querySelector('[name="idempotency_key"]') || {}).value || String(Date.now())
        });
        saveDrafts(rows);
        composer.querySelector("textarea").value = "";
        window.alert("Saved on this phone. It sends when you are back online.");
        return;
      }
      const body = new FormData(composer);
      addBubble("user", text);
      composer.querySelector("textarea").value = "";
      fetch("/chat", {
        method: "POST",
        body: body,
        headers: { Accept: "application/json", "X-CSRF-Token": token }
      }).then(function (resp) { return resp.json(); }).then(function (data) {
        addBubble("assistant", (data && data.reply) || "Saved.");
        const key = composer.querySelector('[name="idempotency_key"]');
        if (key) key.value = Math.random().toString(16).slice(2) + Date.now().toString(16);
        if (data && data.ok && /on your sites/i.test(data.reply || "") && location.pathname === "/") {
          window.setTimeout(function () { location.reload(); }, 600);
        }
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
})();
