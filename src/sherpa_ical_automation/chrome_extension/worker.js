const defaultBookingsUrl = "https://sherpa-online.com/tutor/bookings";
const sherpaHosts = ["sherpa-online.com", "www.sherpa-online.com", "app.sherpa-online.com"];
const bookingsPatterns = sherpaHosts.map((host) => `https://${host}/*bookings*`);
const alarm = "sherpa-calendar";
let activeScan;
let running = false;

async function bridge(config, method, payload) {
  const headers = { Authorization: `Bearer ${config.token}` };
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(config.url, {
    method,
    headers,
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Local Sherpa feed is unavailable");
  return result;
}

function check(manual = false) {
  if (running) return;
  running = true;
  runScan(manual).catch(async (error) => {
    await chrome.action.setBadgeText({ text: "!" });
    await chrome.action.setTitle({ title: `Sherpa Calendar: ${error.message}` });
  }).finally(() => { running = false; });
}

async function runScan(manual) {
  const config = await fetch(chrome.runtime.getURL("config.json"), { cache: "no-store" })
    .then((response) => response.json());
  if (manual) await bridge(config, "PUT");
  const { id } = await bridge(config, "GET");
  if (!id) return;
  let bookingsUrl = config.bookings_url || defaultBookingsUrl;
  if (!config.bookings_url || bookingsUrl === "https://sherpa-online.com/bookings") {
    const openBookings = await chrome.tabs.query({ url: bookingsPatterns });
    const openPage = openBookings.find((tab) => tab.active) ?? openBookings[0];
    if (openPage?.url) {
      const page = new URL(openPage.url);
      if (page.pathname.toLowerCase().includes("bookings")) {
        bookingsUrl = page.origin + page.pathname;
      }
    }
  }
  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  activeScan = { tabId: tab.id, responses: [], bytes: 0 };
  try {
    await chrome.tabs.update(tab.id, { url: bookingsUrl });
    await new Promise((resolve) => setTimeout(resolve, 14_000));
    const current = await chrome.tabs.get(tab.id);
    const result = await bridge(config, "POST", {
      id,
      page: current.url || activeScan.page || "",
      responses: activeScan.responses,
    });
    await chrome.action.setBadgeText({ text: "" });
    await chrome.action.setTitle({ title: `Sherpa Calendar: ${result.count} confirmed bookings` });
  } finally {
    activeScan = undefined;
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!activeScan || sender.tab?.id !== activeScan.tabId || sender.frameId !== 0) return;
  activeScan.page = sender.url;
  if (typeof message.body !== "string" || typeof message.route !== "string" ||
      !["html", "json"].includes(message.kind)) return;
  const size = new TextEncoder().encode(message.body).length;
  if (activeScan.bytes + size <= 500_000 && activeScan.responses.length < 64) {
    activeScan.bytes += size;
    activeScan.responses.push(message);
  }
});

async function start(scanImmediately) {
  await chrome.alarms.create(alarm, { periodInMinutes: 1 });
  check(scanImmediately);
}

chrome.runtime.onInstalled.addListener(() => start(true));
chrome.runtime.onStartup.addListener(() => start(false));
chrome.alarms.onAlarm.addListener(({ name }) => {
  if (name === alarm) check();
});
chrome.action.onClicked.addListener(() => check(true));
