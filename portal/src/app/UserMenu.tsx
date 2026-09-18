/* Menu de usuario (barra superior): quien soy, con que rol, Mi cuenta y Cerrar sesion. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { I, Icon } from "../ui/components";
import { useSession } from "./session";
import { roleMeta } from "./roles";

export default function UserMenu() {
  const { me, role, viewAs, logout } = useSession();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const rm = roleMeta(role);
  const initials = (me?.user.full_name || "?").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  useEffect(() => {
    const out = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", out); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", out); window.removeEventListener("keydown", esc); };
  }, []);

  const exit = async () => {
    setBusy(true);
    try { await logout(); } finally { nav("/login", { replace: true }); }
  };

  return (
    <div className="umenu" ref={box}>
      <button className="umenu__btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} title="Tu cuenta">
        <span className="avatar" style={{ boxShadow: `0 0 0 2px ${rm.tone}` }}>{initials}</span>
        <span className="umenu__name hide-sm">{me?.user.full_name.split(" ")[0]}</span>
        <Icon d="M6 9l6 6 6-6" size={14} />
      </button>
      {open && (
        <div className="umenu__pop" role="menu">
          <div className="umenu__who">
            <b>{me?.user.full_name}</b>
            <span>{me?.user.email}</span>
            <span className="umenu__role" style={{ color: rm.tone }}><i style={{ background: rm.tone }} />{rm.label}{viewAs ? " · vista previa" : ""}</span>
          </div>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/ajustes?tab=cuenta"); }}><Icon d={I.settings} size={16} />Mi cuenta</button>
          <button role="menuitem" className="umenu__out" onClick={exit} disabled={busy}><Icon d={I.logout} size={16} />{busy ? "Cerrando…" : "Cerrar sesión"}</button>
        </div>
      )}
    </div>
  );
}
