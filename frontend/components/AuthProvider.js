"use client";

/**
 * Keeps track of who is signed in, for every page.
 *
 * On first load it asks the backend GET /auth/me:
 *   200 -> user is signed in   (user = their profile)
 *   401 -> nobody signed in    (user = null)
 *
 * Any component can read it with:  const { user, loading } = useAuth();
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // useCallback keeps the same function between renders, so pages can
  // safely list it as an effect dependency.
  const refresh = useCallback(async () => {
    try {
      const me = await apiFetch("/auth/me");
      setUser(me);
      setError(null);
      return me;
    } catch (err) {
      setUser(null);
      // 401 just means "not signed in"; anything else is a real problem.
      setError(err instanceof ApiError && err.status === 401 ? null : err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await apiFetch("/auth/logout", { method: "POST" });
    setUser(null);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, setUser, loading, error, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

/**
 * For pages that need a signed-in, onboarded user.
 * Sends everyone else to the right place:
 *   not signed in      -> /login
 *   profile incomplete -> /onboarding
 * Returns the user once they're allowed to see the page (null before that).
 */
export function useRequireUser() {
  const { user, loading, error } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading || error) return;
    if (!user) router.replace("/login");
    else if (!user.is_onboarded) router.replace("/onboarding");
  }, [user, loading, error, router]);

  return user?.is_onboarded ? user : null;
}
