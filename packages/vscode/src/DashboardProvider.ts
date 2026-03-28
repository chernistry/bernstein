import * as vscode from 'vscode';
import type { DashboardData } from './BernsteinClient';

function getNonce(): string {
  const chars =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  return Array.from(
    { length: 32 },
    () => chars[Math.floor(Math.random() * chars.length)],
  ).join('');
}

function buildHtml(data: DashboardData | null): string {
  const nonce = getNonce();
  const csp =
    `default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';`;

  const stats = data?.stats;
  const statsHtml = stats
    ? `
      <div class="stat"><span class="label">Agents</span><span class="value">${stats.agent_count}</span></div>
      <div class="stat"><span class="label">Open</span><span class="value open">${stats.open}</span></div>
      <div class="stat"><span class="label">Running</span><span class="value running">${stats.claimed}</span></div>
      <div class="stat"><span class="label">Done</span><span class="value done">${stats.done}</span></div>
      <div class="stat"><span class="label">Failed</span><span class="value failed">${stats.failed}</span></div>
      <div class="stat"><span class="label">Cost</span><span class="value">$${stats.total_cost_usd.toFixed(2)}</span></div>`
    : '<div class="offline">Not connected to Bernstein</div>';

  const alertsHtml =
    (data?.alerts ?? [])
      .map((a) => `<div class="alert ${a.level}">${a.message}</div>`)
      .join('') || '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Bernstein</title>
  <style nonce="${nonce}">
    body {
      font-family: var(--vscode-font-family);
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      margin: 0; padding: 8px;
    }
    h3 {
      font-size: 10px; text-transform: uppercase;
      opacity: 0.6; margin: 8px 0 4px; letter-spacing: 0.5px;
    }
    .stats {
      display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 12px;
    }
    .stat {
      background: var(--vscode-editor-background);
      border-radius: 4px; padding: 6px 8px;
    }
    .label { font-size: 10px; opacity: 0.7; display: block; }
    .value { font-size: 18px; font-weight: 600; }
    .open    { color: var(--vscode-charts-blue);   }
    .running { color: var(--vscode-charts-yellow); }
    .done    { color: var(--vscode-charts-green);  }
    .failed  { color: var(--vscode-charts-red);    }
    .alert {
      padding: 4px 8px; border-radius: 3px;
      font-size: 11px; margin-bottom: 4px;
    }
    .alert.warning { background: var(--vscode-inputValidation-warningBackground); }
    .alert.error   { background: var(--vscode-inputValidation-errorBackground);   }
    .offline { color: var(--vscode-disabledForeground); font-size: 12px; padding: 8px; }
  </style>
</head>
<body>
  <h3>Overview</h3>
  <div class="stats">${statsHtml}</div>
  ${alertsHtml ? `<h3>Alerts</h3>${alertsHtml}` : ''}
</body>
</html>`;
}

export class DashboardProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private data: DashboardData | null = null;

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: false };
    webviewView.webview.html = buildHtml(this.data);
  }

  update(data: DashboardData): void {
    this.data = data;
    if (this.view) {
      this.view.webview.html = buildHtml(data);
    }
  }

  /**
   * Opens the full Bernstein dashboard in the default browser.
   * VS Code webviews cannot iframe localhost due to CSP restrictions,
   * so we open externally.
   */
  static openInBrowser(baseUrl: string): void {
    void vscode.env.openExternal(vscode.Uri.parse(`${baseUrl}/dashboard`));
  }
}
