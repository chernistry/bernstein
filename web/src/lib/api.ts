// Single fetch wrapper for /api/v1/* — bearer auth from localStorage.

const TOKEN_KEY = 'bernstein_token';
const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
    // Preserve a useful name for instanceof and devtools display.
    this.name = 'ApiError';
  }
}

function authHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? window.localStorage.getItem(TOKEN_KEY) : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Best-effort body parse for both error and success paths.
 * - JSON content-type → parsed object
 * - Anything else with a body → trimmed text
 * - Empty body → null
 * Never throws; returns null on parse failure.
 */
async function readBody(r: Response): Promise<unknown> {
  const ct = r.headers.get('content-type') ?? '';
  try {
    const text = await r.text();
    if (!text) return null;
    if (ct.includes('application/json')) {
      try {
        return JSON.parse(text);
      } catch {
        // Backend lied about content-type — keep raw text so callers can debug.
        return text;
      }
    }
    return text;
  } catch {
    return null;
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE}${path}`;
  // Only set Content-Type when we actually send a body — some servers reject
  // GET/DELETE with a content-type header, and 204/202 responses confuse CORS preflights.
  const hasBody = init.body != null;
  const baseHeaders: Record<string, string> = { Accept: 'application/json' };
  if (hasBody) baseHeaders['Content-Type'] = 'application/json';
  const r = await fetch(url, {
    ...init,
    headers: {
      ...baseHeaders,
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  });
  if (r.status === 401) {
    if (typeof window !== 'undefined') window.localStorage.removeItem(TOKEN_KEY);
    // Preserve any server-provided error body so callers can surface a reason.
    const body = await readBody(r);
    throw new ApiError(401, body, 'Unauthorized — clear session and re-auth');
  }
  if (!r.ok) {
    const body = await readBody(r);
    throw new ApiError(r.status, body, `${r.status} ${r.statusText} — ${path}`);
  }
  // 204 No Content / 205 Reset Content / explicit empty body.
  if (r.status === 204 || r.status === 205) return undefined as T;
  const ct = r.headers.get('content-type') ?? '';
  // JSON path: parse and return.
  if (ct.includes('application/json')) {
    const text = await r.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch (err) {
      throw new ApiError(
        r.status,
        text,
        `${path} — server claimed application/json but returned unparseable body: ${(err as Error).message}`,
      );
    }
  }
  // Unexpected non-JSON success body. Don't silently return undefined: surface a
  // useful error so callers know the contract was violated (e.g. backend served
  // an HTML 200 from a misconfigured proxy). Empty bodies remain undefined.
  const text = await r.text();
  if (!text) return undefined as T;
  throw new ApiError(
    r.status,
    text,
    `${path} — expected JSON but got ${ct || 'unknown content-type'}`,
  );
}

export const apiGet = <T = unknown>(path: string, init?: RequestInit) =>
  api<T>(path, { ...init, method: 'GET' });
export const apiPost = <T = unknown>(path: string, body?: unknown, init?: RequestInit) =>
  api<T>(path, { ...init, method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined });
export const apiPut = <T = unknown>(path: string, body?: unknown, init?: RequestInit) =>
  api<T>(path, { ...init, method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined });
export const apiDelete = <T = unknown>(path: string, init?: RequestInit) =>
  api<T>(path, { ...init, method: 'DELETE' });
