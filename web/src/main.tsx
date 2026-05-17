import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

const rootEl = document.getElementById('root');
if (!rootEl) {
  // Fail loud rather than silently with a non-null assertion — a missing
  // root means index.html drifted from this entry point and we'd otherwise
  // crash deep inside React with an unhelpful error.
  throw new Error('Bernstein UI: #root element not found in index.html');
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
