/* Trades, photos, gas, miles, and receipts wait on the phone and send when the signal returns.
   The same receipt is not filed twice. */
(function () {
  var KEY = "apt-outbox";
  var box = document.getElementById("apt-outbox");

  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); }
    catch (err) { return []; }
  }
  function write(rows) {
    try { localStorage.setItem(KEY, JSON.stringify(rows)); } catch (err) {}
    render();
  }
  function db() {
    return new Promise(function (resolve, reject) {
      var open = indexedDB.open("apt-outbox", 1);
      open.onupgradeneeded = function () { open.result.createObjectStore("files"); };
      open.onsuccess = function () { resolve(open.result); };
      open.onerror = function () { reject(open.error); };
    });
  }
  function putFile(id, blob) {
    return db().then(function (database) {
      return new Promise(function (resolve, reject) {
        var tx = database.transaction("files", "readwrite");
        tx.objectStore("files").put(blob, id);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }
  function getFile(id) {
    return db().then(function (database) {
      return new Promise(function (resolve, reject) {
        var tx = database.transaction("files", "readonly");
        var ask = tx.objectStore("files").get(id);
        ask.onsuccess = function () { resolve(ask.result || null); };
        ask.onerror = function () { reject(ask.error); };
      });
    });
  }
  function dropFile(id) {
    return db().then(function (database) {
      return new Promise(function (resolve) {
        var tx = database.transaction("files", "readwrite");
        tx.objectStore("files").delete(id);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { resolve(); };
      });
    }).catch(function () {});
  }

  function kindOf(form) {
    if (!form || form.id === "composer" || (form.closest && form.closest("#chat-panel"))) return "";
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return "";
    var action = form.getAttribute("action") || "";
    var path = location.pathname;
    try { path = new URL(action || location.href, location.href).pathname; } catch (err) {}
    if (path.indexOf("/ready-check") !== -1) return "trade";
    if (/\/photo$/.test(path) || form.classList.contains("photo-shot")) return "photo";
    if (path === "/expenses") return "money";
    if (path === "/miles") return "miles";
    return "";
  }
  function ensureKey(form) {
    var input = form.querySelector('input[name="idempotency_key"]');
    if (input && input.value) return input.value;
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "idempotency_key";
      form.appendChild(input);
    }
    if (!input.value) input.value = Math.random().toString(16).slice(2) + Date.now().toString(16);
    return input.value;
  }
  function fieldsOf(form) {
    var fields = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.type === "file" || el.disabled) return;
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) return;
      fields[el.name] = el.value;
    });
    return fields;
  }
  function labelFor(kind, fields, form) {
    if (kind === "trade") {
      var button = form.querySelector("button");
      var text = button ? button.innerText.replace(/\s+/g, " ").trim() : "Make ready";
      return fields.done === "0" ? "Undo " + text : text;
    }
    if (kind === "photo") return "Photo of this unit";
    if (kind === "money") {
      var merchant = fields.merchant ? " at " + fields.merchant : "";
      return (fields.kind || "Receipt") + " $" + (fields.amount || "") + merchant;
    }
    if (fields.reading) return "Odometer " + fields.reading;
    return (fields.miles || "") + " miles" + (fields.note ? " · " + fields.note : "");
  }

  var holdText = "";
  function hold(text) {
    holdText = text;
    render();
  }
  function render() {
    if (!box) return;
    var rows = read();
    if (!rows.length && !holdText) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    if (!rows.length) {
      box.innerHTML = "<p>" + escapeText(holdText) + "</p>";
      return;
    }
    var waiting = rows.filter(function (row) { return !row.confirm; }).length;
    var asking = rows.length - waiting;
    var title = rows.length === 1 ? "1 waiting to send" : rows.length + " waiting to send";
    if (asking && !waiting) title = asking === 1 ? "1 to file" : asking + " to file";
    var html = "";
    if (holdText) html += "<p>" + escapeText(holdText) + "</p>";
    html += "<p>" + title + "</p><ul>";
    rows.forEach(function (row) {
      html += "<li><span>" + escapeText(row.label) + "</span>";
      if (row.note) html += "<small>" + escapeText(row.note) + "</small>";
      if (row.confirm) {
        html += "<button type=\"button\" data-file=\"" + escapeText(row.id) + "\">File this</button>";
        html += "<button type=\"button\" class=\"quiet\" data-drop=\"" + escapeText(row.id) + "\">Not now</button>";
      } else {
        html += "<small>Waiting to send</small>";
      }
      html += "</li>";
    });
    html += "</ul>";
    box.innerHTML = html;
  }
  function escapeText(value) {
    return String(value || "").replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[ch];
    });
  }

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return Promise.resolve(meta.content);
    return fetch("/ready", { credentials: "same-origin" }).then(function (resp) {
      if ((resp.url || "").indexOf("/login") !== -1) return "";
      return resp.text();
    }).then(function (html) {
      var match = String(html || "").match(/csrf-token" content="([^"]+)"/);
      return match ? match[1] : "";
    }).catch(function () { return ""; });
  }

  function postItem(item, token) {
    var body = new FormData();
    body.set("csrf_token", token);
    body.set("idempotency_key", item.id);
    Object.keys(item.fields || {}).forEach(function (name) {
      if (name === "csrf_token" || name === "idempotency_key") return;
      body.set(name, item.fields[name]);
    });
    var ready = item.hasFile ? getFile(item.id).then(function (blob) {
      if (!blob) {
        var missing = new Error("missing");
        missing.code = "missing";
        throw missing;
      }
      body.set("photo", blob, item.fileName || "photo.jpg");
    }) : Promise.resolve();
    return ready.then(function () {
      return fetch(item.url, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-CSRF-Token": token }
      });
    });
  }

  function take(id) {
    var rows = read();
    var item = null;
    write(rows.filter(function (row) {
      if (row.id === id) item = row;
      return row.id !== id;
    }));
    if (item && item.hasFile) dropFile(id);
    return item;
  }

  function flushAutos() {
    if (!navigator.onLine) {
      render();
      return Promise.resolve();
    }
    var autos = read().filter(function (row) { return !row.confirm; });
    if (!autos.length) {
      render();
      return Promise.resolve();
    }
    return csrf().then(function (token) {
      if (!token) {
        autos.forEach(function (row) { row.note = "Sign in to send these."; });
        var rows = read();
        rows.forEach(function (row) {
          autos.forEach(function (auto) { if (auto.id === row.id) row.note = auto.note; });
        });
        write(rows);
        return;
      }
      var chain = Promise.resolve(false);
      autos.forEach(function (item) {
        chain = chain.then(function (sentAny) {
          return sendOne(item, token, true).then(function (sent) { return sentAny || sent; });
        });
      });
      return chain.then(function (sent) {
        var active = document.activeElement;
        var typing = active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
        if (sent && !typing) location.reload();
      });
    });
  }

  function sendOne(item, token, auto) {
    return postItem(item, token).then(function (resp) {
      var url = resp.url || "";
      if (url.indexOf("/login") !== -1) {
        note(item.id, "Sign in to send these.");
        return false;
      }
      if (resp.status === 409) {
        note(item.id, "That did not save. Drop it or try the unit again.");
        return false;
      }
      if (!resp.ok) {
        note(item.id, "That did not send. It is still here.");
        return false;
      }
      take(item.id);
      return true;
    }).catch(function (err) {
      if (err && err.code === "missing") {
        note(item.id, "The photo is no longer on this phone.");
        return false;
      }
      if (auto) note(item.id, "Still waiting for signal.");
      return false;
    });
  }

  function note(id, text) {
    var rows = read();
    rows.forEach(function (row) { if (row.id === id) row.note = text; });
    write(rows);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var kind = kindOf(form);
    if (!kind) return;
    ensureKey(form);
    if (navigator.onLine) return;
    var fields = fieldsOf(form);
    var fileInput = form.querySelector('input[type="file"]');
    var file = fileInput && fileInput.files && fileInput.files[0];
    if (kind === "photo" && !file) {
      event.preventDefault();
      event.stopImmediatePropagation();
      hold("Choose a photo first.");
      return;
    }
    if (file && file.size > 12 * 1024 * 1024) {
      event.preventDefault();
      event.stopImmediatePropagation();
      hold("That photo is over 12 MB.");
      return;
    }
    var rows = read();
    if (rows.length >= 30) {
      event.preventDefault();
      event.stopImmediatePropagation();
      hold("Waiting list is full. Send or drop one first.");
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    holdText = "";
    delete fields.csrf_token;
    var item = {
      id: fields.idempotency_key,
      kind: kind,
      url: new URL(form.getAttribute("action") || location.href, location.href).pathname,
      fields: fields,
      label: labelFor(kind, fields, form),
      confirm: false,
      hasFile: !!file,
      fileName: file ? file.name : ""
    };
    var store = file ? putFile(item.id, file) : Promise.resolve();
    store.then(function () {
      rows = read();
      if (!rows.some(function (row) { return row.id === item.id; })) rows.push(item);
      write(rows);
      if (fileInput) fileInput.value = "";
    }).catch(function () {});
  }, true);

  if (box) {
    box.addEventListener("click", function (event) {
      var fileId = event.target.getAttribute && event.target.getAttribute("data-file");
      var dropId = event.target.getAttribute && event.target.getAttribute("data-drop");
      if (dropId) {
        take(dropId);
        return;
      }
      if (!fileId) return;
      if (!navigator.onLine) {
        note(fileId, "Wait for signal, then tap File this.");
        return;
      }
      var item = read().filter(function (row) { return row.id === fileId; })[0];
      if (!item) return;
      csrf().then(function (token) {
        if (!token) {
          note(fileId, "Sign in to send these.");
          return;
        }
        sendOne(item, token, false).then(function (sent) { if (sent) location.reload(); });
      });
    });
  }

  window.addEventListener("online", flushAutos);
  render();
  flushAutos();
})();
