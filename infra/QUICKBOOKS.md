# QuickBooks Online: paquete mensual para la contadora

Crimson es la fuente y **nunca escribe en QuickBooks**. Cada mes arma un ZIP que la contadora revisa e importa ella
misma con *Configuración ⚙ → Importar datos*. No hay conexión por API, tokens ni variables de entorno nuevas.

Portal: **Contabilidad → QuickBooks**. Código: `api/app/services/qb_package.py`, `api/app/routers/quickbooks.py`,
`portal/src/modules/accounting/contable/QbSetup.tsx` y `PaqueteMensual.tsx`.

## Pasos en Crimson

1. **Plan de cuentas**: subir la «Lista de cuentas» exportada de QBO (CSV o Excel). Se reconocen las cabeceras en
   español e inglés sin importar tildes ni plural (p. ej. `Nombre de la cuenta, Tipo de cuenta, Tipo de detalles,
   Moneda, Saldo de QuickBooks, Saldo bancario`); las columnas extra, filas de título y totales se ignoran.
2. **Mapeo**: cada categoría de gasto apunta a una cuenta de QBO (es la columna *Account* de las facturas de
   proveedor). Una categoría sin cuenta bloquea el mes.
3. **Revisar mes**: muestra qué saldría y los avisos; *Excel de control (borrador)* para revisar; **Marcar como
   revisado** (queda quién y cuándo; con errores no se puede). *Quitar «revisado»* lo vuelve a abierto.
4. **Generar paquete**: solo de un mes revisado. Descarga el ZIP, cierra el mes (`exportado`) y registra quién, cuándo
   y qué documentos salieron (`accounting_export` con `kind = paquete` y `manifest`). Volver a generar un mes cerrado:
   solo el administrador y con motivo (arma el mismo paquete).
5. **Asientos (avanzado)**: el CSV de asientos de diario de siempre, como respaldo manual.

Lo que cambie después de un paquete **no se vuelve a mandar**: sale en la hoja *Ajustes* del paquete siguiente
(cambió, se anuló o se borró) para corregirlo a mano en QBO. Un documento nuevo con fecha de un mes que ya tuvo
paquete entra en el paquete siguiente marcado «De un mes anterior».

## Qué trae el ZIP

| Archivo | Para qué |
|---|---|
| `1_clientes.csv` / `1_clientes_actualizar.csv` | Clientes nuevos / que cambiaron desde el paquete anterior |
| `2_proveedores.csv` / `2_proveedores_actualizar.csv` | Ídem proveedores |
| `3_facturas_venta[_n].csv` | Facturas de venta (Invoices), una fila por línea |
| `4_facturas_proveedor[_n].csv` | Facturas de proveedor (Bills), una fila por tarifa de IVA |
| `control_AAAA-MM.xlsx` | Resumen, Ventas, Gastos (IVA por tarifa, acreditable y no), Cobros, Pagos a proveedores, Notas de crédito, D-104 (referencial), Ajustes, Contactos, Respaldos |
| `respaldos/ventas`, `respaldos/compras` | XML enviados/recibidos, respuestas de Hacienda y PDF guardados (tope 60 MB; lo que no entra queda listado) |
| `LEEME.txt` | Pasos de importación |

Columnas de las facturas de venta: `Invoice no, Customer, Invoice Date, Due Date, Memo, Item(Product/Service),
Item Description, Item Quantity, Item Rate, Item Amount, Item Tax Code, Item Tax Amount, Currency Code, Exchange Rate`.
Facturas de proveedor: `Bill no, Supplier, Bill Date, Due Date, Memo, Account, Line Description, Line Amount,
Line Tax Code, Line Tax Amount, Currency Code, Exchange Rate`. Montos sin IVA, con punto decimal; fechas en el formato
configurado (por defecto `dd/mm/aaaa`).

## Cómo importarlo en QuickBooks (contadora)

1. Revisar el Excel de control.
2. *Configuración ⚙ → Importar datos*, en este orden: **Clientes**, **Proveedores**, **Facturas**, **Facturas de
   proveedor** (archivos numerados en orden).
3. Mapear las columnas (las que no aparezcan: «No aplica»), elegir el formato de fecha y la opción **sin impuestos
   incluidos (Exclusive)**.
4. Emparejar los códigos `IVA 13%`, `IVA 4%`, `IVA 2%`, `IVA 1%`, `IVA 0%`, `Exento` con los códigos de impuesto de la
   empresa. `IVA no acreditable` va con un código de 0 %: ese IVA ya está sumado al gasto.
5. Una sola vez: crear los productos/servicios *Ventas de equipos*, *Ventas de servicios* y *Ventas de proyectos*, cada
   uno con su cuenta de ingresos; y activar «números de transacción personalizados» para conservar los números.
6. Cobros, pagos a proveedores y notas de crédito no se importan por archivo: están en el Excel con fecha, medio,
   referencia y banco para registrarlos o conciliarlos con el banco.

## Límites y lo que no está confirmado

Confirmado en la ayuda de Intuit (edición global): importación de **Facturas** y **Facturas de proveedor** desde CSV;
hasta **100 facturas y 1000 filas** por archivo (el paquete parte los archivos sin cortar una factura); columnas
obligatorias de facturas (*Invoice number, Customer, Invoice date, Due date, Item amount, Item tax code*) y de facturas
de proveedor (*Bill no., Supplier, Bill Date, Due Date, Account, Line Amount, Line Tax Code*); opción Exclusive /
Inclusive; el monto de impuesto enviado reemplaza el cálculo de QBO; no se importan descuentos, notas de crédito ni
montos negativos; los nombres de cliente/proveedor deben ser únicos (un mismo nombre como cliente y proveedor choca:
el paquete lo avisa).

No confirmado (verificar con el archivo de ejemplo que baja QBO en *Importar datos*): los nombres exactos de las
columnas opcionales (`Item Tax Amount`, `Currency Code`, `Exchange Rate`, `Tax ID` de contactos); si la importación de
contactos acepta moneda (con multimoneda, un cliente o proveedor en USD debe existir con esa moneda antes de importar
sus facturas); si QBO en español espera coma decimal.

Fuentes: ayuda de QuickBooks (global) «Import multiple invoices at once» e «Import your bills in QuickBooks Online»,
y «Common questions about importing data to QuickBooks Online».
