/* Enabled only when the backend starts with QIMING_LIVE_PREVIEW=true. */
(function () {
  'use strict';

  let previous = null;
  let stopped = false;
  const restoreKey = 'qh_preview_page';

  document.addEventListener('DOMContentLoaded', () => {
    try {
      const page = sessionStorage.getItem(restoreKey);
      sessionStorage.removeItem(restoreKey);
      const button = Array.from(document.querySelectorAll('.nav-item[data-page]'))
        .find(item => item.dataset.page === page);
      if (button) button.click();
    } catch (_) {
      // Live preview also works with browser storage disabled.
    }
  });

  async function replaceStylesheet(link, version) {
    const replacement = link.cloneNode();
    const url = new URL(link.href, location.href);
    url.searchParams.set('_qiming_preview', version);
    replacement.href = url.href;
    return new Promise(resolve => {
      const timeout = setTimeout(() => finish(false), 8000);
      function finish(loaded) {
        clearTimeout(timeout);
        replacement.onload = null;
        replacement.onerror = null;
        if (loaded) link.remove();
        else replacement.remove();
        resolve(loaded);
      }
      replacement.onload = () => finish(true);
      replacement.onerror = () => finish(false);
      link.after(replacement);
    });
  }

  async function poll() {
    let requestTimeout;
    try {
      if (document.hidden) return;
      const controller = new AbortController();
      requestTimeout = setTimeout(() => controller.abort(), 5000);
      const response = await fetch('/api/dev/frontend-version', {
        cache: 'no-store',
        signal: controller.signal
      });
      if (!response.ok) return;
      const current = await response.json();
      if (!current.enabled) {
        stopped = true;
        return;
      }
      if (previous && previous.reload !== current.reload) {
        const activePage = document.querySelector('.nav-item.active');
        try {
          if (activePage) sessionStorage.setItem(restoreKey, activePage.dataset.page);
        } catch (_) {
          // A reload does not depend on session storage.
        }
        stopped = true;
        location.reload();
        return;
      }
      if (previous) {
        const updates = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
          .filter(link => {
            const url = new URL(link.href, location.href);
            return url.origin === location.origin && current.styles[url.pathname] &&
              previous.styles[url.pathname] !== current.styles[url.pathname];
          })
          .map(link => replaceStylesheet(link, current.styles[new URL(link.href).pathname]));
        const loaded = await Promise.all(updates);
        if (loaded.some(success => !success)) return;
      }
      previous = current;
    } catch (_) {
      // Reconnect after a temporary backend or network interruption.
    } finally {
      clearTimeout(requestTimeout);
      if (!stopped) setTimeout(poll, 1000);
    }
  }

  poll();
})();
