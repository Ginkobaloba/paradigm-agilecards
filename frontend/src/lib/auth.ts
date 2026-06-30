/**
 * Bearer-token storage on the client. SessionStorage by default so closing
 * the tab logs you out; that's deliberate for a private dashboard. Switch
 * to localStorage later if you want persistent sessions.
 */

const KEY = "agile-cards-board.token";

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    /* swallow; private mode etc. */
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* swallow */
  }
}
