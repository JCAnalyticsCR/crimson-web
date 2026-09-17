import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, auth, type Me } from "../lib/api";

type Session = { me: Me | null; loading: boolean; login: (email: string, password: string, totp?: string) => Promise<void>; logout: () => Promise<void>; reload: () => Promise<void>; theme: string; toggleTheme: () => void; toast: (msg: string, tone?: "ok" | "bad") => void };
const Ctx = createContext<Session>(null!);
export const useSession = () => useContext(Ctx);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("crimson-theme") || "light"; } catch { return "light"; } });
  const [toastState, setToast] = useState<{ msg: string; tone: "ok" | "bad" } | null>(null);

  const reload = useCallback(async () => {
    try { setMe(await api<Me>("/auth/me")); } catch { setMe(null); }
  }, []);

  useEffect(() => {
    // Al abrir: intentar refresh por cookie -> /me
    (async () => {
      try { await api<Me>("/auth/me"); } catch { /* sin token: api() ya intento refresh */ }
      await reload();
      setLoading(false);
    })();
    const off = auth.onChange(() => { if (!auth.token) setMe(null); });
    return () => { off(); };
  }, [reload]);

  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("crimson-theme", theme); } catch { /* privado */ } }, [theme]);

  const login = async (email: string, password: string, totp?: string) => {
    const r = await api<{ access_token: string }>("/auth/login", { method: "POST", json: { email, password, totp_code: totp || null } });
    auth.set(r.access_token);
    await reload();
  };
  const logout = async () => { try { await api("/auth/logout", { method: "POST" }); } finally { auth.set(null); setMe(null); } };
  const toast = (msg: string, tone: "ok" | "bad" = "ok") => { setToast({ msg, tone }); setTimeout(() => setToast(null), 3200); };

  return (
    <Ctx.Provider value={{ me, loading, login, logout, reload, theme, toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")), toast }}>
      {children}
      {toastState && <div className={`toast toast--${toastState.tone}`} role="status">{toastState.msg}</div>}
    </Ctx.Provider>
  );
}
