/* Buscador de catalogo o de clientes. Un <select> no sirve cuando hay cientos de productos
   (la lista de Eurocomp trae mas de 200), y la API devuelve 100 por pagina: se busca escribiendo. */
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";

export type LookupItem = { id: number; label: string; hint?: string };

export function Lookup({ value, placeholder, onSelect, fetcher, disabled }: {
  value: string;
  placeholder?: string;
  onSelect: (item: LookupItem | null, text: string) => void;
  fetcher: (q: string) => Promise<LookupItem[]>;
  disabled?: boolean;
}) {
  const [text, setText] = useState(value);
  const [items, setItems] = useState<LookupItem[]>([]);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const id = useMemo(() => `lk${Math.random().toString(36).slice(2, 8)}`, []);

  useEffect(() => { setText(value); }, [value]);
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => { fetcher(text).then(setItems).catch(() => setItems([])); }, 200);
    return () => clearTimeout(t);
  }, [text, open, fetcher]);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  return (
    <div className="lookup" ref={box}>
      <input
        id={id}
        className="input"
        disabled={disabled}
        placeholder={placeholder}
        value={text}
        onFocus={() => setOpen(true)}
        onChange={(e) => { setText(e.target.value); setOpen(true); onSelect(null, e.target.value); }}
      />
      {open && items.length > 0 && (
        <ul className="lookup__list">
          {items.slice(0, 12).map((it) => (
            <li key={it.id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setText(it.label); setOpen(false); onSelect(it, it.label); }}>
                <b>{it.label}</b>{it.hint && <small>{it.hint}</small>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const searchProducts = async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ items: { id: number; name: string; code: string; unit: string }[] }>(`/products?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  return r.items.map((p) => ({ id: p.id, label: p.name, hint: p.code }));
};

export const searchCustomers = async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ items: { id: number; name: string; id_number: string | null }[] }>(`/customers?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  return r.items.map((c) => ({ id: c.id, label: c.name, hint: c.id_number || undefined }));
};
