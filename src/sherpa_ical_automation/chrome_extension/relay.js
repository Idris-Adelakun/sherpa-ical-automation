window.addEventListener("message", (event) => {
  if (event.source === window && event.origin === location.origin &&
      event.data?.channel === "sherpa-calendar") {
    const { route, kind, body } = event.data;
    chrome.runtime.sendMessage({ route, kind, body }).catch(() => {});
  }
});
