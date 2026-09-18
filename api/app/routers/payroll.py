"""Planillas: colaboradores, corridas (borrador -> aprobada -> pagada), colillas y tasas configurables."""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import BankAccount, Employee, Expense, ExpenseCategory, PayrollLine, PayrollRun
from ..services import payroll as pr
from ..services.documents import audit
from ..services.render import money
from ..services.totals import d

router = APIRouter(prefix="/payroll", tags=["planillas"])


# ---------- Tasas ----------
class RatesIn(BaseModel):
    ccss_worker: float = Field(ge=0, le=100)
    ccss_employer: float = Field(ge=0, le=100)
    brackets: list[list[float | None]]
    child_credit: float = Field(ge=0)
    spouse_credit: float = Field(ge=0)
    aguinaldo: float = Field(ge=0, le=100)
    vacaciones: float = Field(ge=0, le=100)
    year: int

    @model_validator(mode="after")
    def _brackets(self):
        if not self.brackets or self.brackets[-1][0] is not None:
            raise ValueError("El ultimo tramo debe ser abierto (limite vacio)")
        limits = [b[0] for b in self.brackets[:-1]]
        if any(x is None for x in limits) or limits != sorted(limits):
            raise ValueError("Los limites de los tramos deben ir de menor a mayor")
        return self


@router.get("/settings")
def get_rates(p: Principal = Depends(require("payroll", "ver"))):
    return {**pr.rates_for(p.tenant.settings), "defaults": pr.DEFAULTS}


@router.put("/settings")
def put_rates(data: RatesIn, p: Principal = Depends(require("payroll", "configurar")), db: Session = Depends(get_db)):
    st = dict(p.tenant.settings or {})
    st["payroll"] = data.model_dump()
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")
    audit(db, p.tenant.id, p.user.id, "payroll_rates", "tenant", p.tenant.id, data.model_dump(), ip=p.ip)
    db.commit()
    return pr.rates_for(p.tenant.settings)


# ---------- Colaboradores ----------
class EmployeeIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    id_number: str | None = None
    email: str | None = None
    phone: str | None = None
    position: str | None = None
    salary: Decimal = Field(ge=0)
    frequency: str = Field("mensual", pattern="^(mensual|quincenal)$")
    start_date: date | None = None
    iban: str | None = None
    children: int = Field(0, ge=0, le=20)
    spouse_credit: bool = False
    active: bool = True


def _emp_out(e: Employee) -> dict:
    return {
        k: getattr(e, k)
        for k in ("id", "name", "id_number", "email", "phone", "position", "salary", "frequency", "start_date", "iban", "children", "spouse_credit", "active")
    }


@router.get("/employees")
def employees(p: Principal = Depends(require("payroll", "ver")), db: Session = Depends(get_db)):
    return [_emp_out(e) for e in db.scalars(select(Employee).where(Employee.tenant_id == p.tenant.id).order_by(Employee.active.desc(), Employee.name))]


@router.post("/employees", status_code=201)
def employee_create(data: EmployeeIn, p: Principal = Depends(require("payroll", "crear")), db: Session = Depends(get_db)):
    e = Employee(tenant_id=p.tenant.id, **data.model_dump())
    db.add(e)
    db.commit()
    return _emp_out(e)


@router.put("/employees/{eid}")
def employee_update(eid: int, data: EmployeeIn, p: Principal = Depends(require("payroll", "editar")), db: Session = Depends(get_db)):
    e = db.get(Employee, eid)
    if not e or e.tenant_id != p.tenant.id:
        raise HTTPException(404, "Colaborador no encontrado")
    for k, v in data.model_dump().items():
        setattr(e, k, v)
    db.commit()
    return _emp_out(e)


# ---------- Corridas ----------
class RunItem(BaseModel):
    employee_id: int
    overtime: Decimal = Field(Decimal(0), ge=0)
    bonus: Decimal = Field(Decimal(0), ge=0)
    other_deductions: Decimal = Field(Decimal(0), ge=0)


class RunIn(BaseModel):
    period_start: date
    period_end: date
    frequency: str = Field("mensual", pattern="^(mensual|quincenal)$")
    items: list[RunItem] = Field(default_factory=list)  # ajustes opcionales por colaborador
    notes: str | None = None


def _totals(run: PayrollRun) -> None:
    for f in ("gross", "ccss_worker", "income_tax", "other_deductions", "net", "ccss_employer", "provisions"):
        setattr(run, f, sum((d(getattr(ln, f)) for ln in run.lines), Decimal(0)))


def _run_out(run: PayrollRun) -> dict:
    return {
        "id": run.id,
        "period_start": run.period_start,
        "period_end": run.period_end,
        "frequency": run.frequency,
        "status": run.status,
        "gross": run.gross,
        "ccss_worker": run.ccss_worker,
        "income_tax": run.income_tax,
        "other_deductions": run.other_deductions,
        "net": run.net,
        "ccss_employer": run.ccss_employer,
        "provisions": run.provisions,
        "employer_cost": d(run.gross) + d(run.ccss_employer),
        "rates": run.rates,
        "expense_id": run.expense_id,
        "notes": run.notes,
        "lines": [
            {
                k: getattr(ln, k)
                for k in (
                    "id",
                    "employee_id",
                    "employee_name",
                    "base",
                    "overtime",
                    "bonus",
                    "other_deductions",
                    "gross",
                    "ccss_worker",
                    "income_tax",
                    "net",
                    "ccss_employer",
                    "provisions",
                )
            }
            for ln in sorted(run.lines, key=lambda x: x.employee_name)
        ],
    }


def _own_run(db: Session, rid: int, tid: int) -> PayrollRun:
    run = db.get(PayrollRun, rid)
    if not run or run.tenant_id != tid:
        raise HTTPException(404, "Planilla no encontrada")
    return run


@router.get("/runs")
def runs(p: Principal = Depends(require("payroll", "ver")), db: Session = Depends(get_db)):
    return [
        {k: v for k, v in _run_out(r).items() if k != "lines"} | {"people": len(r.lines)}
        for r in db.scalars(select(PayrollRun).where(PayrollRun.tenant_id == p.tenant.id).order_by(PayrollRun.period_end.desc()).limit(60))
    ]


@router.post("/runs", status_code=201)
def run_create(data: RunIn, p: Principal = Depends(require("payroll", "crear")), db: Session = Depends(get_db)):
    if data.period_end < data.period_start:
        raise HTTPException(422, "El periodo termina antes de empezar")
    emps = db.scalars(
        select(Employee).where(Employee.tenant_id == p.tenant.id, Employee.active, Employee.frequency == data.frequency).order_by(Employee.name)
    ).all()
    if not emps:
        raise HTTPException(409, f"No hay colaboradores activos con pago {data.frequency}")
    rates = pr.rates_for(p.tenant.settings)
    adj = {i.employee_id: i for i in data.items}
    run = PayrollRun(tenant_id=p.tenant.id, period_start=data.period_start, period_end=data.period_end, frequency=data.frequency, rates=rates, notes=data.notes)
    for e in emps:
        a = adj.get(e.id) or RunItem(employee_id=e.id)
        calc = pr.compute_line(d(e.salary), data.frequency, rates, a.overtime, a.bonus, a.other_deductions, e.children, e.spouse_credit)
        run.lines.append(PayrollLine(employee_id=e.id, employee_name=e.name, **calc))
    _totals(run)
    db.add(run)
    db.flush()
    audit(db, p.tenant.id, p.user.id, "create", "payroll_run", run.id, {"people": len(emps)}, ip=p.ip)
    db.commit()
    return _run_out(run)


@router.get("/runs/{rid}")
def run_get(rid: int, p: Principal = Depends(require("payroll", "ver")), db: Session = Depends(get_db)):
    return _run_out(_own_run(db, rid, p.tenant.id))


class LineAdjust(BaseModel):
    overtime: Decimal = Field(Decimal(0), ge=0)
    bonus: Decimal = Field(Decimal(0), ge=0)
    other_deductions: Decimal = Field(Decimal(0), ge=0)


@router.put("/runs/{rid}/lines/{lid}")
def line_adjust(rid: int, lid: int, data: LineAdjust, p: Principal = Depends(require("payroll", "editar")), db: Session = Depends(get_db)):
    run = _own_run(db, rid, p.tenant.id)
    if run.status != "borrador":
        raise HTTPException(409, "Solo se editan planillas en borrador")
    ln = next((x for x in run.lines if x.id == lid), None)
    if not ln:
        raise HTTPException(404, "Linea no encontrada")
    e = db.get(Employee, ln.employee_id)
    calc = pr.compute_line(d(e.salary), run.frequency, run.rates, data.overtime, data.bonus, data.other_deductions, e.children, e.spouse_credit)
    for k, v in calc.items():
        setattr(ln, k, v)
    _totals(run)
    db.commit()
    return _run_out(run)


@router.post("/runs/{rid}/approve")
def run_approve(rid: int, p: Principal = Depends(require("payroll", "aprobar")), db: Session = Depends(get_db)):
    run = _own_run(db, rid, p.tenant.id)
    if run.status != "borrador":
        raise HTTPException(409, f"La planilla ya esta {run.status}")
    run.status = "aprobada"
    audit(db, p.tenant.id, p.user.id, "approve", "payroll_run", run.id, {"net": str(run.net)}, ip=p.ip)
    db.commit()
    return _run_out(run)


class PayIn(BaseModel):
    date: date
    bank_account_id: int | None = None


@router.post("/runs/{rid}/pay")
def run_pay(rid: int, data: PayIn, p: Principal = Depends(require("payroll", "aprobar")), db: Session = Depends(get_db)):
    """Marca pagada y registra el gasto de planilla (bruto + cargas patronales) en contabilidad."""
    run = _own_run(db, rid, p.tenant.id)
    if run.status != "aprobada":
        raise HTTPException(409, "Primero apruebe la planilla")
    if data.bank_account_id:
        ba = db.get(BankAccount, data.bank_account_id)
        if not ba or ba.tenant_id != p.tenant.id:
            raise HTTPException(404, "Cuenta bancaria no encontrada")
    cat = db.scalar(select(ExpenseCategory).where(ExpenseCategory.tenant_id == p.tenant.id, ExpenseCategory.name == "Planilla"))
    if not cat:
        cat = ExpenseCategory(tenant_id=p.tenant.id, name="Planilla")
        db.add(cat)
        db.flush()
    cost = d(run.gross) + d(run.ccss_employer)
    e = Expense(
        tenant_id=p.tenant.id,
        category_id=cat.id,
        description=f"Planilla {run.frequency} {run.period_start.isoformat()} a {run.period_end.isoformat()} (bruto + CCSS patronal)",
        date=data.date,
        currency="CRC",
        subtotal=cost,
        tax_rate=0,
        tax_amount=0,
        total=cost,
        iva_credit="no_credito",
        bank_account_id=data.bank_account_id,
        reference=f"PLANILLA-{run.id}",
        status="pagado",
        created_by=p.user.id,
    )
    db.add(e)
    db.flush()
    run.status, run.expense_id = "pagada", e.id
    audit(db, p.tenant.id, p.user.id, "pay", "payroll_run", run.id, {"expense_id": e.id, "cost": str(cost)}, ip=p.ip)
    db.commit()
    return _run_out(run)


@router.delete("/runs/{rid}", status_code=204)
def run_delete(rid: int, p: Principal = Depends(require("payroll", "editar")), db: Session = Depends(get_db)):
    run = _own_run(db, rid, p.tenant.id)
    if run.status != "borrador":
        raise HTTPException(409, "Solo se eliminan planillas en borrador")
    db.delete(run)
    db.commit()


@router.get("/runs/{rid}/slip/{lid}", response_class=HTMLResponse)
def slip(rid: int, lid: int, p: Principal = Depends(require("payroll", "ver")), db: Session = Depends(get_db)):
    """Colilla de pago imprimible."""
    run = _own_run(db, rid, p.tenant.id)
    ln = next((x for x in run.lines if x.id == lid), None)
    if not ln:
        raise HTTPException(404, "Linea no encontrada")
    e = db.get(Employee, ln.employee_id)
    esc = html.escape
    rows = [
        ("Salario base del periodo", ln.base, ""),
        ("Horas extra", ln.overtime, ""),
        ("Bonificaciones", ln.bonus, ""),
        ("Salario bruto", ln.gross, "b"),
        (f"CCSS trabajador ({run.rates.get('ccss_worker')} %)", -d(ln.ccss_worker), ""),
        ("Impuesto al salario", -d(ln.income_tax), ""),
        ("Otras deducciones", -d(ln.other_deductions), ""),
        ("Neto a pagar", ln.net, "b"),
    ]
    body = "".join(f'<tr class="{c}"><td>{esc(label)}</td><td class="n">{money(v, "CRC")}</td></tr>' for label, v, c in rows)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Colilla {esc(ln.employee_name)}</title>
<style>body{{font-family:Manrope,system-ui,sans-serif;color:#15131a;max-width:640px;margin:32px auto;padding:0 16px}}
h1{{font-size:20px;margin:0}} .m{{color:#6b6570;font-size:13px}} table{{width:100%;border-collapse:collapse;margin-top:20px}}
td{{padding:9px 0;border-bottom:1px solid #eee;font-size:14px}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
tr.b td{{font-weight:700;border-top:2px solid #15131a}} .brand{{border-left:4px solid #e2233a;padding-left:12px;margin-bottom:18px}}
@media print{{button{{display:none}}}}</style></head><body>
<div class="brand"><h1>{esc(p.tenant.name)}</h1><div class="m">Colilla de pago · planilla {esc(run.frequency)} {run.period_start.isoformat()} a {run.period_end.isoformat()}</div></div>
<b>{esc(ln.employee_name)}</b><div class="m">{esc(e.id_number or "")} · {esc(e.position or "")}{" · IBAN " + esc(e.iban) if e.iban else ""}</div>
<table>{body}</table>
<p class="m">Aporte patronal CCSS y provisiones de aguinaldo y vacaciones no se rebajan del salario.</p>
<button onclick="print()">Imprimir</button></body></html>"""
