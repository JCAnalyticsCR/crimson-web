import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, auth, bootstrap, type Me } from "../lib/api";
import { canAny, type Perms } from "./roles";

type Session = {
  me: Me | null; loading: boolean;
  login: (email: string, password: string, totp?: string) => Promise<void>; logout: () => Promise<void>; reload: () => Promise<void>;
  theme: string; toggleTheme: () => void; toast: (msg: string, tone?: "ok" | "bad") => void;
  /** Rol con el que se dibuja la interfaz (el real, o el que el admin esta previsualizando). */
  role: string; perms: Perms; allows: (need?: string) => boolean;
  viewAs: string | null; setViewAs: (role: string | null) => void; matrix: Record<string, Perms>;
};
const Ctx = createContext<Session>(null!);
export const useSession = () => useContext(Ctx);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("crimson-theme") || "light"; } catch { return "light"; } });
  const [toastState, setToast] = useState<{ msg: string; tone: "ok" | "bad" } | null>(null);
  const [viewAs, setViewAsState] = useState<string | null>(() => { try { return sessionStorage.getItem("crimson-view-as"); } catch { return null; } });
  const [matrix, setMatrix] = useState<Record<string, Perms>>({});

  const reload = useCallback(async () => {
    try { setMe(await api<Me>("/auth/me")); } catch { setMe(null); }
  }, []);

  useEffect(() => {
    (async () => {
      if (await bootstrap()) await reload();
      setLoading(false);
    })();
    const off = auth.onChange(() => { if (!auth.token) setMe(null); });
    return () => { off(); };
  }, [reload]);

  useEffect(() => {
    if (me?.role === "admin") api<{ matrix: Record<string, Perms> }>("/auth/roles").then((r) => setMatrix(r.matrix)).catch(() => {});
  }, [me?.role]);

  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("crimson-theme", theme); } catch { /* privado */ } }, [theme]);

  const setViewAs = (r: string | null) => {
    setViewAsState(r);
    try { if (r) sessionStorage.setItem("crimson-view-as", r); else sessionStorage.removeItem("crimson-view-as"); } catch { /* privado */ }
  };

  const login = async (email: string, password: string, totp?: string) => {
    const r = await api<{ access_token: string }>("/auth/login", { method: "POST", json: { email, password, totp_code: totp || null } });
    auth.set(r.access_token);
    setViewAs(null);
    try { sessionStorage.setItem("crimson-welcome", "1"); } catch { /* privado */ }
    await reload();
  };
  const logout = async () => { try { await api("/auth/logout", { method: "POST" }); } finally { auth.set(null); setMe(null); setViewAs(null); } };
  const toast = (msg: string, tone: "ok" | "bad" = "ok") => { setToast({ msg, tone }); setTimeout(() => setToast(null), 3200); };

  // La vista previa solo existe para administradores y solo reduce permisos (nunca amplia lo que la API permite).
  const previewing = me?.role === "admin" && viewAs && matrix[viewAs] ? viewAs : null;
  const role = previewing || me?.role || "";
  const perms: Perms = useMemo(() => (previewing ? matrix[previewing] : me?.permissions) || {}, [previewing, matrix, me?.permissions]);
  const allows = useCallback((need?: string) => canAny(perms, need), [perms]);

  return (
    <Ctx.Provider value={{ me, loading, login, logout, reload, theme, toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")), toast, role, perms, allows, viewAs: previewing, setViewAs, matrix }}>
      {children}
      {toastState && <div className={`toast toast--${toastState.tone}`} role="status">{toastState.msg}</div>}
    </Ctx.Provider>
  );
}
