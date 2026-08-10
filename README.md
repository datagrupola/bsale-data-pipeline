# Bsale data pipeline

Migración completa del pipeline de extracción de Bsale desde Google Apps Script
hacia Python, GitHub Actions y Neon/PostgreSQL.

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

La carga productiva escribe en Neon/PostgreSQL. El orden obligatorio es:

```text
daily_sales → products_daily (y pieces_sold) → categories_daily
```

Cada etapa sólo continúa cuando la anterior termina correctamente.

## Configurar el secreto

En el repositorio de GitHub:

1. `Settings`
2. `Secrets and variables`
3. `Actions`
4. `New repository secret`
5. Crear los dos secretos siguientes:

   | Nombre | Valor |
   |---|---|
   | `BSALE_ACCESS_TOKEN` | Token privado de Bsale |
   | `DATABASE_URL` | Cadena de conexión privada de Neon/PostgreSQL |

No guardar secretos en archivos ni commits.

## Sincronización diaria a Neon

El workflow **Sync Bsale daily data to Neon** se ejecuta todos los días a las
**01:30, hora de Ciudad de México**, y procesa el día fiscal anterior. Ejecuta
en una sola cadena:

1. ventas y pagos hacia `daily_sales`;
2. detalle SKU hacia `products_daily` y piezas hacia `daily_sales`;
3. resumen hacia `categories_daily`.

Si una etapa falla, las posteriores no se ejecutan. Para reprocesar un día,
abre `Actions` → **Sync Bsale daily data to Neon** → **Run workflow** e indica
la fecha fiscal. No corras los tres extractores manuales por separado para una
carga productiva.

### Dejarlo preparado, pero apagado

El horario ya está definido, pero el procesamiento automático queda **apagado
por defecto**. Sólo se activa cuando la variable de repositorio
`DAILY_SYNC_ENABLED` vale exactamente `true`; si no existe, se considera
apagado. Las ejecuciones manuales continúan disponibles para pruebas o
reprocesos controlados.

Después de subir el workflow, para encender la operación diaria con un solo
comando desde una terminal autenticada en GitHub CLI:

```bash
gh variable set DAILY_SYNC_ENABLED --body true --repo datagrupola/bsale-data-pipeline
```

Para detenerla otra vez:

```bash
gh variable set DAILY_SYNC_ENABLED --body false --repo datagrupola/bsale-data-pipeline
```

También puede cambiarse desde GitHub en `Settings` → `Secrets and variables` →
`Actions` → `Variables`. No hay que editar YAML, secretos ni ejecutar los tres
procesos por separado. El primer día se procesará a las 01:30 hora CDMX.

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
export DATABASE_URL="POSTGRES_CONNECTION_STRING"
python -m src.extract_daily_sales --date 2026-07-27 --write-db
python -m src.extract_products --start-date 2026-07-27 --write-db
python -m src.build_categories_daily --start-date 2026-07-27 --write-db
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
