import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import {
  captureAuthTokenFromFragment,
  captureInstallPrompt,
  registerServiceWorker,
} from './lib/pwa';

const rootEl = document.getElementById('root');
if (!rootEl) {
  // Fail loud rather than silently with a non-null assertion - a missing
  // root means index.html drifted from this entry point and we'd otherwise
  // crash deep inside React with an unhelpful error.
  throw new Error('Bernstein UI: #root element not found in index.html');
}

// PWA bootstrap. Runs before React mounts so the auth-token fragment is
// captured even if the SPA itself crashes during boot.
captureAuthTokenFromFragment();
captureInstallPrompt();

// Service worker registration is best-effort and intentionally fire-and-
// forget: a failed SW registration must not block the SPA boot.
//
// We defer to ``window.load`` so the SW download competes with idle
// network instead of the SPA's initial bundle. ``registerServiceWorker``
// is itself safe to call before ``load``, but the original guidance from
// web.dev still holds for cold installs on weak mobile links.
if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void registerServiceWorker('/sw.js', '/');
  });
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
