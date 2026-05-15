// Single fetch wrapper for /api/v1/* — bearer auth from localStorage.

const TOKEN_KEY = 'bernstein_token';
const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
  }
}

function authHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? window.localStorage.getItem(TOKEN_KEY) : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE}${path}`;
  const r = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  });
  if (r.status === 401) {
    if (typeof window !== 'undefined') window.localStorage.removeItem(TOKEN_KEY);
    throw new ApiError(401, null, 'Unauthorized — clear session and re-auth');
  }
  if (!r.ok) {
    let body: unknown = null;
    try {
      body = await r.json();
    } catch {
      // ignore
    }
    throw new ApiError(r.status, body, `${r.status} ${r.statusText} — ${path}`);
  }
  // Some endpoints return empty body on 204 / 202.
  const ct = r.headers.get('content-type') ?? '';
  if (!ct.includes('application/json')) return undefined as T;
  return (await r.json()) as T;
}

export const apiGet = <T = unknown>(path: string) => api<T>(path, { method: 'GET' });
export const apiPost = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });
export const apiPut = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined });
export const apiDelete = <T = unknown>(path: string) => api<T>(path, { method: 'DELETE' });
