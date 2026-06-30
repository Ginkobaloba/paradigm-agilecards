/**
 * Tiny auth state. The token lives in sessionStorage; this hook exposes
 * presence as React state so guards can re-render on sign-in/out.
 */

import { useCallback, useEffect, useState } from "react";

import { clearToken, getToken, setToken } from "../lib/auth";

export function useAuth() {
  const [token, setTokenState] = useState<string | null>(() => getToken());

  // Sync across tabs.
  useEffect(() => {
    const handler = (e: StorageEvent): void => {
      if (e.key === "agile-cards-board.token") {
        setTokenState(e.newValue);
      }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const signIn = useCallback((newToken: string) => {
    setToken(newToken);
    setTokenState(newToken);
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setTokenState(null);
  }, []);

  return { token, isAuthed: token !== null, signIn, signOut };
}
