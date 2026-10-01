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
      const clip = introVideo.getAttribute("data-src") || "/static/intro/open.mp4?v=3";
      if (!introVideo.getAttribute("src")) introVideo.setAttribute("src", clip);
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

  document.querySelectorAll(".property-switch select").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (sel.form) sel.form.submit();
    });
  });

  const token = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const SESSION_EXPIRED = "Session expired, sign in again";

  document.querySelectorAll("form").forEach(function (form) {
    if (form.method && form.method.toUpperCase() === "GET") return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = token;
    form.appendChild(input);
  });
  const nativeFetch = window.fetch;
  if (nativeFetch) {
    window.fetch = function (url, opts) {
      opts = opts || {};
      opts.headers = opts.headers || {};
      if (opts.headers instanceof Headers) {
        if (!opts.headers.has("X-CSRF-Token")) opts.headers.set("X-CSRF-Token", token);
      } else if (!opts.headers["X-CSRF-Token"] && !opts.headers["x-csrf-token"]) {
        opts.headers["X-CSRF-Token"] = token;
      }
      return nativeFetch(url, opts);
    };
  }

  function storeGet(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }
  function storeSet(key, value) {
    try { localStorage.setItem(key, value); } catch (err) {}
  }

  function isHtmlResponse(resp) {
    const ctype = ((resp && resp.headers && resp.headers.get("content-type")) || "").toLowerCase();
    return ctype.indexOf("text/html") !== -1;
  }

  function isJsonResponse(resp) {
    const ctype = ((resp && resp.headers && resp.headers.get("content-type")) || "").toLowerCase();
    return ctype.indexOf("application/json") !== -1;
  }

  function sessionGone(resp) {
    return !resp || resp.status === 401 || resp.status === 403 || isHtmlResponse(resp);
  }

  function readJson(resp) {
    if (sessionGone(resp)) {
      const err = new Error(SESSION_EXPIRED);
      err.code = "session";
      return Promise.reject(err);
    }
    if (!resp.ok || !isJsonResponse(resp)) {
      const err = new Error("bad-response");
      err.code = "bad";
      return Promise.reject(err);
    }
    return resp.json();
  }

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
          .then(readJson)
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
          })
          .catch(function (err) {
            results.innerHTML = "";
            const emptyMessage = document.createElement("p");
            emptyMessage.className = "lead";
            emptyMessage.textContent = (err && err.code === "session") ? SESSION_EXPIRED : "Could not search places.";
            results.appendChild(emptyMessage);
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
    try { return JSON.parse(storeGet("apt-drafts") || "[]"); }
    catch (err) { return []; }
  }
  function saveDrafts(rows) {
    try { storeSet("apt-drafts", JSON.stringify(rows)); } catch (err) {}
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
    document.body.classList.toggle("chat-open", !!open);
    openChat.hidden = !!open;
    openChat.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const box = panel.querySelector("textarea");
      if (box) box.focus();
      const thread = document.getElementById("thread");
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
    try { sessionStorage.setItem("apt-chat-open", open ? "1" : "0"); } catch (err) {}
  }
  if (openChat) openChat.addEventListener("click", function () { setChat(true); });
  if (closeChat) closeChat.addEventListener("click", function () { setChat(false); });
  if (newChat) {
    newChat.addEventListener("click", function () {
      fetch("/chat/new", {
        method: "POST",
        headers: { "X-CSRF-Token": token, Accept: "application/json" }
      })
        .then(readJson)
        .then(function (data) {
          const threadEl = document.getElementById("thread");
          if (!threadEl) return;
          threadEl.innerHTML = "";
          addBubble("assistant", (data && data.reply) || "New chat. Tell me what you're doing.");
        })
        .catch(function (err) {
          addBubble("assistant", (err && err.code === "session") ? SESSION_EXPIRED : "Could not start a new chat.");
        });
    });
  }
  try { setChat(sessionStorage.getItem("apt-chat-open") === "1"); }
  catch (err) { setChat(false); }

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
        .then(function (resp) {
          if (!resp.ok || !isJsonResponse(resp)) {
            const err = new Error(sessionGone(resp) ? "session" : "bad");
            err.code = sessionGone(resp) ? "session" : "bad";
            err.resp = resp;
            return Promise.reject(err);
          }
          return resp.json();
        })
        .then(function (data) {
          const closed = (data && data.closed_ids) || (card && card.dataset.pendingId ? [card.dataset.pendingId] : []);
          dropCards(closed);
          if (card && card.parentNode) card.remove();
          addBubble("assistant", (data && data.reply) || (form.classList.contains("discard-form") ? "Discarded." : "Saved."));
        })
        .catch(function (err) {
          if (err && err.code === "session") {
            addBubble("assistant", SESSION_EXPIRED);
            return;
          }
          if (err && err.code === "bad") {
            addBubble("assistant", "That didn't save. Try again.");
            return;
          }
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
        .then(readJson)
        .then(function (data) {
          addBubble("assistant", (data && data.reply) || "Got it.");

          const proposals = (data && data.proposals) || (data && data.proposal ? [data.proposal] : []);
          proposals.forEach(renderProposal);
          if (data && data.default_proposal) renderProposal(data.default_proposal);
          const key = composer.querySelector('[name="idempotency_key"]');
          if (key) key.value = Math.random().toString(16).slice(2) + Date.now().toString(16);
        })
        .catch(function (err) {
          addBubble("assistant", (err && err.code === "session") ? SESSION_EXPIRED : "That didn't send. Try again.");
        });

    });
  }

  async function flush() {
    if (!navigator.onLine) return;
    const rows = drafts();
    if (!rows.length || !token) return;
    const left = [];
    let warned = false;
    for (const row of rows) {
      const body = new FormData();
      body.set("csrf_token", token);
      body.set("message", row.message);
      body.set("idempotency_key", row.idempotency_key);
      try {
        const resp = await fetch("/chat", { method: "POST", body: body, headers: { "X-CSRF-Token": token, Accept: "application/json" } });
        if (!resp.ok || !isJsonResponse(resp) || sessionGone(resp)) {
          left.push(row);
          if (sessionGone(resp) && !warned) {
            addBubble("assistant", SESSION_EXPIRED);
            warned = true;
          }
          continue;
        }
      } catch (err) {
        left.push(row);
      }
    }
    saveDrafts(left);
  }
  window.addEventListener("online", flush);
  flush();

  const sharing = document.body.getAttribute("data-share") === "1";
  let pingWarned = false;
  async function ping() {
    if (!sharing || !navigator.onLine || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(function (pos) {
      fetch("/api/ping", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token, Accept: "application/json" },
        body: JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude })
      }).then(function (resp) {
        if (sessionGone(resp) && !pingWarned) {
          pingWarned = true;
          addBubble("assistant", SESSION_EXPIRED);
        }
      }).catch(function () {});
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
        storeSet("apt-installed", "1");
      }
  window.addEventListener("appinstalled", function () {
    storeSet("apt-installed", "1");
    if (install) install.hidden = true;
  });
  function alreadyInstalled() {
    return storeGet("apt-installed") === "1" || standalone;
  }
  let installEvent = null;
  window.addEventListener("beforeinstallprompt", function (event) {
    if (alreadyInstalled()) return;
    event.preventDefault();
    installEvent = event;
    if (install && storeGet("apt-install-hide") !== "1") install.hidden = false;
  });
  if (install && !alreadyInstalled() && storeGet("apt-install-hide") !== "1") {
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
          storeSet("apt-installed", "1");
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
        if (choice && choice.outcome === "accepted") storeSet("apt-installed", "1");
        install.hidden = true;
        installEvent = null;
      });
    });
  }
  if (installSkip) {
    installSkip.addEventListener("click", function () {
      storeSet("apt-install-hide", "1");
      install.hidden = true;
    });
  }

  const mapBox = document.getElementById("map");
  const mapDataEl = document.getElementById("map-data");
  if (mapBox && mapDataEl && window.L) {
    let data = { pins: [], home: null };
    try { data = JSON.parse(mapDataEl.textContent || "{}"); } catch (err) {}
    if (L.Icon && L.Icon.Default) {
      L.Icon.Default.imagePath = "/static/vendor/leaflet/images/";
    }
    const map = L.map("map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "&copy; OpenStreetMap" }).addTo(map);
    const bounds = [];
    (data.pins || []).forEach(function (pin) {
      const marker = L.marker([pin.lat, pin.lng]).addTo(map);
      const wrap = document.createElement("span");
      const link = document.createElement("a");
      link.setAttribute("href", pin.href || "#");
      link.textContent = pin.name || "";
      wrap.appendChild(link);
      wrap.appendChild(document.createTextNode(" · " + (pin.city || "")));
      marker.bindPopup(wrap);
      bounds.push([pin.lat, pin.lng]);
    });
    if (data.home) {
      L.circleMarker([data.home.lat, data.home.lng], { radius: 8 }).addTo(map).bindPopup(data.home.label || "");
      bounds.push([data.home.lat, data.home.lng]);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [24, 24] });
    else map.setView([31.85, -102.37], 6);
  }

  const providerInfoEl = document.getElementById("provider-info");
  const providerSelect = document.getElementById("provider");
  const modelSelect = document.getElementById("model");
  const hint = document.getElementById("provider-hint");
  if (providerInfoEl && providerSelect && modelSelect) {
    let providerInfo = [];
    try { providerInfo = JSON.parse(providerInfoEl.textContent || "[]"); } catch (err) { providerInfo = []; }
    function fillModels() {
      const current = providerInfo.find(function (item) { return item.id === providerSelect.value; }) || providerInfo[0] || {};
      if (hint) hint.textContent = current.hint || "";
      modelSelect.innerHTML = "";
      (current.models || []).forEach(function (name) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        modelSelect.appendChild(option);
      });
    }
    providerSelect.addEventListener("change", fillModels);
    fillModels();
  }
})();
