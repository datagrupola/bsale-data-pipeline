# Bsale data pipeline

Primera etapa de migración del módulo `syncDailySalesChino.gs`.

## Alcance actual

Este repositorio contiene únicamente el extractor diario de:

- documentos Bsale por sucursal;
- ventas tipo `10`;
- devoluciones tipo `39`;
- ajustes tipo `40`;
- pagos por fecha;
- efectivo reconstruido como residual;
- terminal tipos `2`, `6` y `16`;
- Flux tipo `17`;
- otros medios de pago;
- validación `pagos_total - venta_neta`.

Todavía no escribe en Supabase ni Google Sheets.

## Configurar el secreto

En el repositorio de GitHub:

1. `Settings`
2. `Secrets and variables`
3. `Actions`
4. `New repository secret`
5. Nombre: `BSALE_ACCESS_TOKEN`
6. Valor: token privado de Bsale

No guardar el token en archivos ni commits.

## Ejecutar en GitHub

1. Abrir `Actions`.
2. Seleccionar `Extract Bsale daily sales`.
3. Pulsar `Run workflow`.
4. Escribir una fecha `YYYY-MM-DD`.
5. Descargar el artefacto JSON generado.

## Ejecutar localmente

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export BSALE_ACCESS_TOKEN="TOKEN"
python -m src.extract_daily_sales --date 2026-07-27
```

## Validación inicial

La primera prueba operativa debe ejecutarse para una fecha ya procesada por
Apps Script. Después se comparan, sucursal por sucursal:

- venta bruta;
- devoluciones;
- ajustes;
- venta neta;
- número de documentos;
- efectivo;
- terminal;
- Flux;
- otros pagos;
- total de pagos;
- diferencia de pagos.

No debe programarse la ejecución automática hasta que esa comparación sea
exacta.
