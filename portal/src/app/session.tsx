import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ALLY_PREVIEW_KEY, api, auth, bootstrap, readAllyPreview, type AllyPreview, type Me } from "../lib/api";
import { canAny, inRoleMenu, type Perms } from "./roles";

type Session = {
  me: Me | null; loading: boolean;
  /** newPassword: entró con una contraseña temporal (Ajustes → Usuarios → Restablecer) y crea la suya en el mismo login. */
  login: (email: string, password: string, totp?: string, newPassword?: string) => Promise<void>; logout: () => Promise<void>; reload: () => Promise<void>;
  theme: string; toggleTheme: () => void; toast: (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void;
  /** Rol con el que se dibuja la interfaz (el real, o el que el admin esta previsualizando). */
  role: string; perms: Perms; allows: (need?: string) => boolean;
  /** Lo que la cuenta REALMENTE puede (sin vista previa): numero de Aprobaciones y huecos "oculto en vista previa". */
  realAllows: (need?: string) => boolean;
  /** La seccion (ruta) esta en el menu explicito del rol (roles.ts); sin lista, si. No es seguridad: la API manda. */
  inMenu: (to: string) => boolean;
  viewAs: string | null; setViewAs: (role: string | null) => void; matrix: Record<string, Perms>;
  /** "Ver como este aliado" (solo admin): el portal de aliados con los datos reales de esa empresa, solo lectura. */
  allyPreview: AllyPreview | null; setAllyPreview: (v: AllyPreview | null) => void;
  /** Vuelve a pedir la matriz de roles (p. ej. despues de cambiar "Quien ve costos" en Ajustes). */
  reloadMatrix: () => void;
};
/** Boton dentro del aviso (p. ej. "Deshacer" despues de mandar algo a la papelera). */
export type ToastAction = { label: string; run: () => void };
const Ctx = createContext<Session>(null!);
export const useSession = () => useContext(Ctx);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("crimson-theme") || "light"; } catch { return "light"; } });
  const [toastState, setToast] = useState<{ msg: string; tone: "ok" | "bad"; action?: ToastAction } | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const [viewAs, setViewAsState] = useState<string | null>(() => { try { return sessionStorage.getItem("crimson-view-as"); } catch { return null; } });
  const [matrix, setMatrix] = useState<Record<string, Perms>>({});
  const [allyPv, setAllyPv] = useState<AllyPreview | null>(() => readAllyPreview());

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

  const isAdmin = me?.role === "admin";
  const reloadMatrix = useCallback(() => {
    if (isAdmin) api<{ matrix: Record<string, Perms> }>("/auth/roles").then((r) => setMatrix(r.matrix)).catch(() => {});
  }, [isAdmin]);
  useEffect(() => { reloadMatrix(); }, [reloadMatrix]);

  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("crimson-theme", theme); } catch { /* privado */ } }, [theme]);

  const setViewAs = (r: string | null) => {
    setViewAsState(r);
    try { if (r) sessionStorage.setItem("crimson-view-as", r); else sessionStorage.removeItem("crimson-view-as"); } catch { /* privado */ }
    if (!r) setAllyPreview(null);
  };
  // "Salir" de la vista previa del aliado borra las dos llaves (rol y empresa)
  const setAllyPreview = (v: AllyPreview | null) => {
    setAllyPv(v);
    try { if (v) sessionStorage.setItem(ALLY_PREVIEW_KEY, JSON.stringify(v)); else sessionStorage.removeItem(ALLY_PREVIEW_KEY); } catch { /* privado */ }
    if (v) { setViewAsState(v.role); try { sessionStorage.setItem("crimson-view-as", v.role); } catch { /* privado */ } }
  };

  const login = async (email: string, password: string, totp?: string, newPassword?: string) => {
    const r = await api<{ access_token: string }>("/auth/login", { method: "POST", json: { email, password, totp_code: totp || null, new_password: newPassword || null } });
    auth.set(r.access_token);
    setViewAs(null);
    try { sessionStorage.setItem("crimson-welcome", "1"); } catch { /* privado */ }
    await reload();
  };
  const logout = async () => { try { await api("/auth/logout", { method: "POST" }); } finally { auth.set(null); setMe(null); setViewAs(null); } };
  // Un aviso nuevo reemplaza al anterior y reinicia su tiempo (antes el temporizador viejo cerraba el nuevo).
  // Con boton (Deshacer) dura mas: hay que alcanzar a leerlo y tocarlo.
  const toast = useCallback((msg: string, tone: "ok" | "bad" = "ok", action?: ToastAction) => {
    window.clearTimeout(toastTimer.current);
    setToast({ msg, tone, action });
    toastTimer.current = window.setTimeout(() => setToast(null), action ? 8000 : 3200);
  }, []);

  // La vista previa solo existe para administradores y solo reduce permisos (nunca amplia lo que la API permite).
  // Un rol aliado sin empresa elegida no es vista previa (AppShell abre el selector de aliado).
  const allyPreview = me?.role === "admin" && allyPv?.company_id ? allyPv : null;
  const aliadoSinEmpresa = (viewAs === "aliado" || viewAs === "tecnico_aliado") && !allyPreview;
  const previewing = me?.role === "admin" && viewAs && matrix[viewAs] && !aliadoSinEmpresa ? viewAs : null;
  const role = previewing || me?.role || "";
  const perms: Perms = useMemo(() => (previewing ? matrix[previewing] : me?.permissions) || {}, [previewing, matrix, me?.permissions]);
  const allows = useCallback((need?: string) => canAny(perms, need), [perms]);
  const realAllows = useCallback((need?: string) => canAny(me?.permissions, need), [me?.permissions]);
  // en vista previa `role` ya es el previsualizado: el menu es el de ese rol
  const inMenu = useCallback((to: string) => inRoleMenu(role, to), [role]);

  return (
    <Ctx.Provider value={{ me, loading, login, logout, reload, theme, toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")), toast, role, perms, allows, realAllows, inMenu, viewAs: previewing, setViewAs, matrix, reloadMatrix, allyPreview, setAllyPreview }}>
      {children}
      {toastState && (
        <div className={`toast toast--${toastState.tone}`} role="status">
          <span>{toastState.msg}</span>
          {toastState.action && (
            <button type="button" className="toast__action" onClick={() => { const a = toastState.action!; window.clearTimeout(toastTimer.current); setToast(null); a.run(); }}>{toastState.action.label}</button>
          )}
        </div>
      )}
    </Ctx.Provider>
  );
}
