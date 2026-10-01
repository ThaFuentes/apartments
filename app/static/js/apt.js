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
              const emptyMessage = document.createElement("p");
              emptyMessage.className = "lead";
              emptyMessage.textContent = "Not on your sites yet. In chat, ask to add “" + q + "” from its city to your sites.";
              results.appendChild(emptyMessage);
              return;
            }
            rows.forEach(function (row) {
              const button = document.createElement("button");
              button.type = "button";
              const name = document.createElement("span");
              name.textContent = row.name || "";
              const city = document.createElement("small");
              city.textContent = row.city || "";
              button.append(name, city);
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
      list.querySelectorAll("[data-building-block]").forEach(function (block) {
        const any = Array.prototype.some.call(block.querySelectorAll("[data-unit-card]"), function (card) { return !card.hidden; });
        block.hidden = !any;
        if (q && any) block.open = true;
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
      who.textContent = thread.getAttribute("data-assistant") || "Apt";
      bubble.appendChild(who);
    }
    bubble.appendChild(document.createTextNode(text));
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    bubble.appendChild(time);
    thread.appendChild(bubble);
    thread.scrollTop = thread.scrollHeight;
  }

  function hiddenField(form, name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value || "";
    form.appendChild(input);
  }

  function renderProposal(proposal) {
    const thread = document.getElementById("thread");
    if (!thread || !proposal || !proposal.id || !["pending", "needs_answer"].includes(proposal.status)) return;
    const existingCard = thread.querySelector('[data-pending-id="' + String(proposal.id) + '"]');

    const payload = proposal.payload || {};
    const waitingFor = payload.waiting_for || "";
    const card = document.createElement("section");
    card.className = "bubble card " + proposal.status;
    card.dataset.pendingId = String(proposal.id);
    card.setAttribute("aria-label", proposal.status === "pending" ? "Approval required" : "Answer required");

    const head = document.createElement("div");
    head.className = "approval-head";
    const icon = document.createElement("span");
    icon.className = "approval-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = proposal.status === "pending" ? "✓" : "?";
    const heading = document.createElement("div");
    heading.className = "approval-heading";
    const state = document.createElement("span");
    state.className = "approval-state";
    state.textContent = proposal.status === "pending" ? "Review before saving" : "Needs your answer";
    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = String(proposal.summary || proposal.tool || "Review change").split(/\r?\n/)[0];
    heading.append(state, title);
    head.append(icon, heading);
    card.appendChild(head);

    const changes = proposal.changes || [];
    if (changes.length) {
      const list = document.createElement("ul");
      list.className = "changes";
      list.setAttribute("aria-label", "Proposed changes");
      changes.forEach(function (row) {
        const item = document.createElement("li");
        const field = document.createElement("span");
        field.className = "field";
        field.textContent = row.field || "Change";
        const value = document.createElement("span");
        value.className = "value";
        const before = document.createElement("s");
        before.textContent = row.before == null ? "—" : String(row.before);
        const arrow = document.createElement("span");
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "→";
        const after = document.createElement("strong");
        after.textContent = row.after == null ? "—" : String(row.after);
        value.append(before, arrow, after);
        item.append(field, value);
        list.appendChild(item);
      });
      card.appendChild(list);
    }

    const note = document.createElement("p");
    note.className = "card-note";
    note.textContent = proposal.status === "pending"
      ? "Nothing changes until you save this item."
      : (waitingFor === "default_confirm" ? "Should this be your default property for now?" : "Waiting on your answer. Answer here or in the chat.");
    card.appendChild(note);

    const actions = document.createElement("div");
    actions.className = "actions";
    if (proposal.status === "pending") {
      const save = document.createElement("form");
      save.method = "post";
      save.action = "/pending/" + encodeURIComponent(proposal.id) + "/confirm";
      save.className = "approve-form";
      hiddenField(save, "csrf_token", token);
      hiddenField(save, "next", thread.dataset.next || window.location.pathname);
      const saveButton = document.createElement("button");
      saveButton.type = "submit";
      saveButton.textContent = "Save this item";
      save.appendChild(saveButton);
      actions.appendChild(save);
    } else if (waitingFor === "property_confirm" || waitingFor === "default_confirm") {
      const yes = document.createElement("button");
      yes.type = "button";
      yes.className = "quiet card-answer";
      yes.dataset.answer = "yes";
      yes.textContent = waitingFor === "default_confirm" ? "Yes, make it my default" : "Yes, this site";
      const no = document.createElement("button");
      no.type = "button";
      no.className = "ghost card-answer";
      no.dataset.answer = "no";
      no.textContent = waitingFor === "default_confirm" ? "No, not for now" : "No, choose another";
      actions.append(yes, no);
    }
    if (proposal.status === "pending" || ["property_confirm", "default_confirm", "property", "city", "unit"].includes(waitingFor)) {
      const discard = document.createElement("form");
      discard.method = "post";
      discard.action = "/pending/" + encodeURIComponent(proposal.id) + "/discard";
      discard.className = "discard-form";
      hiddenField(discard, "csrf_token", token);
      hiddenField(discard, "next", thread.dataset.next || window.location.pathname);
      const discardButton = document.createElement("button");
      discardButton.type = "submit";
      discardButton.className = "ghost";
      discardButton.textContent = proposal.status === "pending" ? "Don't save" : "Cancel this item";
      discard.appendChild(discardButton);
      actions.appendChild(discard);
    }
    if (actions.childNodes.length) card.appendChild(actions);
    if (proposal.status === "pending" && ["plan_trip", "plan_day", "record_unit_visit", "log_work", "log_expense"].includes(proposal.tool)) {
      const details = document.createElement("details");
      details.className = "card-edit";
      const summary = document.createElement("summary");
      summary.textContent = "Edit details before saving";
      details.appendChild(summary);
      const form = document.createElement("form");
      form.method = "post";
      form.action = "/pending/" + encodeURIComponent(proposal.id) + "/edit";
      form.className = "card-edit-form";
      hiddenField(form, "csrf_token", token);
      hiddenField(form, "next", thread.dataset.next || window.location.pathname);
      const fields = proposal.tool === "plan_trip" || proposal.tool === "plan_day"
        ? [["property_name", "Property"], ["city", "City"], ["purpose", "Work"], ["starts_on", "Date"]]
        : proposal.tool === "log_expense"
          ? [["kind", "Kind"], ["amount", "Amount"], ["merchant", "Where"], ["odometer", "Odometer"]]
          : [["unit_number", "Unit"], ["title", "Work"]];
      fields.forEach(function (fieldInfo) {
        const label = document.createElement("label");
        label.textContent = fieldInfo[1];
        const input = document.createElement("input");
        input.name = fieldInfo[0];
        input.value = fieldInfo[0] === "amount" && payload.amount_cents != null
          ? (Number(payload.amount_cents) / 100).toFixed(2)
          : (payload[fieldInfo[0]] == null ? "" : String(payload[fieldInfo[0]]));
        if (fieldInfo[0] === "amount") input.inputMode = "decimal";
        if (fieldInfo[0] === "odometer") input.inputMode = "numeric";
        if (fieldInfo[0] === "starts_on") input.placeholder = "YYYY-MM-DD";
        label.appendChild(input);
        form.appendChild(label);
      });
      const update = document.createElement("button");
      update.type = "submit";
      update.className = "quiet";
      update.textContent = "Update review";
      form.appendChild(update);
      details.appendChild(form);
      card.appendChild(details);
    }
    if (existingCard) existingCard.replaceWith(card);
    else thread.appendChild(card);
    thread.scrollTop = thread.scrollHeight;
  }

  function dropCards(ids) {
    const threadEl = document.getElementById("thread");
    if (!threadEl || !ids) return;
    ids.forEach(function (id) {
      const card = threadEl.querySelector('[data-pending-id="' + String(id) + '"]');
      if (card) card.remove();
    });
  }

  const thread = document.getElementById("thread");
  if (thread) {
    thread.addEventListener("click", function (event) {
      const answer = event.target.closest("[data-answer]");
      if (!answer || !composer) return;
      const answerBox = composer.querySelector("textarea");
      answerBox.value = answer.dataset.answer || "";
      if (composer.requestSubmit) composer.requestSubmit();
      else composer.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    thread.addEventListener("submit", function (event) {
      const form = event.target.closest(".approve-form, .discard-form");
      if (!form) return;
      event.preventDefault();
      const card = form.closest("[data-pending-id]");
      const body = new FormData(form);
      fetch(form.action, {
        method: "POST",
        body: body,
        headers: { Accept: "application/json", "X-CSRF-Token": token }
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          const closed = (data && data.closed_ids) || (card && card.dataset.pendingId ? [card.dataset.pendingId] : []);
          dropCards(closed);
          if (card && card.parentNode) card.remove();
          addBubble("assistant", (data && data.reply) || (form.classList.contains("discard-form") ? "Discarded." : "Saved."));
        })
        .catch(function () {
          form.submit();
        });
    });
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
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          addBubble("assistant", (data && data.reply) || "Got it.");

          const proposals = (data && data.proposals) || (data && data.proposal ? [data.proposal] : []);
          proposals.forEach(renderProposal);
          if (data && data.default_proposal) renderProposal(data.default_proposal);
          const key = composer.querySelector('[name="idempotency_key"]');
          if (key) key.value = Math.random().toString(16).slice(2) + Date.now().toString(16);
        })
        .catch(function () {
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
  const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;      if (standalone) {
        try { localStorage.setItem("apt-installed", "1"); } catch (err) {}
      }
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
