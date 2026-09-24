(function () {
  const token = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
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

  if (composer) {
    composer.addEventListener("submit", function (event) {
      if (navigator.onLine) return;
      const text = (composer.querySelector("textarea").value || "").trim();
      event.preventDefault();
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
