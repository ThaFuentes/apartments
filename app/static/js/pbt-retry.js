/* Retry a busy answer instead of showing the user an error.
   The server allows one write per person at a time and answers the second
   concurrent write with 429 + Retry-After. Nothing is lost: this sends the
   same request again after that many seconds. A 503 busy answer (the worker
   ran out of time) is treated the same way. A real 401/403/422 is returned
   untouched so the page can show it. */
(function () {
  var orig = window.fetch;
  if (!orig) return;
  var MAX_TRIES = 2;

  function sameOrigin(input) {
    try {
      var raw = typeof input === "string" ? input : (input && input.url) || "";
      return new URL(raw, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function wait(ms) {
    return new Promise(function (ok) { setTimeout(ok, ms); });
  }

  window.fetch = function (input, init) {
    if (!sameOrigin(input)) return orig.call(window, input, init);
    var tries = 0;
    function run() {
      return orig.call(window, input, init).then(function (res) {
        if (res.status !== 429 && res.status !== 503) return res;
        if (tries >= MAX_TRIES) return res;
        if (init && init.signal && init.signal.aborted) return res;
        tries += 1;
        var secs = parseInt(res.headers.get("Retry-After") || "1", 10);
        if (!secs || secs < 1) secs = 1;
        if (secs > 8) secs = 8;
        return wait(secs * 1000).then(run);
      });
    }
    return run();
  };
})();
