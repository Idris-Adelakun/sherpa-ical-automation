(() => {
  if (!location.pathname.toLowerCase().includes("bookings")) return;
  const sherpaHosts = new Set([
    "sherpa-online.com", "www.sherpa-online.com", "app.sherpa-online.com",
  ]);
  const maxCharacters = 1_000_000;

  function send(url, kind, body) {
    if (typeof body !== "string" || body.length > maxCharacters) return;
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.port || !sherpaHosts.has(parsed.hostname)) return;
    const route = parsed.origin + parsed.pathname;
    window.postMessage({ channel: "sherpa-calendar", route, kind, body }, location.origin);
  }

  function potentiallyBookings(body) {
    return body.length < 4_096 || /"(meetingDateTime|when)"/.test(body);
  }

  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    const response = originalFetch.apply(this, args);
    response.then(async (result) => {
      if (result.ok && result.headers.get("content-type")?.includes("json")) {
        const body = await result.clone().text();
        if (potentiallyBookings(body)) send(result.url, "json", body);
      }
    }).catch(() => {});
    return response;
  };

  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      if (this.status < 200 || this.status >= 300 ||
          !this.getResponseHeader("content-type")?.includes("json")) return;
      let body;
      if (this.responseType === "json") {
        body = JSON.stringify(this.response);
      } else if (this.responseType === "" || this.responseType === "text") {
        body = this.responseText;
      }
      if (typeof body === "string" && potentiallyBookings(body)) {
        send(this.responseURL, "json", body);
      }
    }, { once: true });
    return originalSend.apply(this, args);
  };

  window.addEventListener("DOMContentLoaded", () => {
    const scripts = [...document.scripts]
      .map((script) => script.textContent)
      .filter((text) => text.includes("meetingDateTime"));
    if (scripts.length) {
      send(location.href, "html", scripts.map((text) => `<script>${text}</script>`).join(""));
    }
  }, { once: true });
})();
