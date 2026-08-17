-- 002_add_pacas_schema.sql
-- Pacas MX: capa transaccional, devoluciones, agregados BI y metas.

BEGIN;

-- 1. Documentos Bsale de Pacas MX
CREATE TABLE IF NOT EXISTS public.pacas_documents (
    document_id BIGINT PRIMARY KEY,
    emission_date DATE NOT NULL,

    office_id INTEGER NOT NULL,
    document_type_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL,

    document_number BIGINT,
    serial_number TEXT,

    total_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    net_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    tax_amount NUMERIC(14,2) NOT NULL DEFAULT 0,

    user_id BIGINT,
    seller_count INTEGER NOT NULL DEFAULT 0,

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (movement_type IN ('SALE', 'RETURN')),
    CHECK (document_type_id IN (10, 39, 45, 46)),
    CHECK (total_amount >= 0),
    CHECK (net_amount >= 0),
    CHECK (tax_amount >= 0)
);

-- 2. Sellers asociados a cada documento
CREATE TABLE IF NOT EXISTS public.pacas_document_sellers (
    document_id BIGINT NOT NULL
        REFERENCES public.pacas_documents(document_id)
        ON DELETE CASCADE,

    seller_id BIGINT NOT NULL,
    seller_name TEXT NOT NULL,

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (document_id, seller_id)
);

-- 3. Resolución de devoluciones hacia la venta/vendedor original
CREATE TABLE IF NOT EXISTS public.pacas_return_allocations (
    return_document_id BIGINT NOT NULL
        REFERENCES public.pacas_documents(document_id)
        ON DELETE CASCADE,

    return_detail_id BIGINT NOT NULL,
    related_detail_id BIGINT,

    original_document_id BIGINT,
    original_seller_id BIGINT,
    original_seller_name TEXT,

    return_amount NUMERIC(14,2) NOT NULL DEFAULT 0,

    resolution_status TEXT NOT NULL DEFAULT 'RESOLVED',

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (return_document_id, return_detail_id),

    CHECK (return_amount >= 0),

    CHECK (
        resolution_status IN (
            'RESOLVED',
            'MISSING_RELATED_DETAIL',
            'ORIGINAL_DOCUMENT_NOT_FOUND',
            'ORIGINAL_SELLER_NOT_FOUND',
            'MULTIPLE_ORIGINAL_SELLERS'
        )
    )
);

-- 4. Venta diaria total de Pacas MX
CREATE TABLE IF NOT EXISTS public.pacas_daily_sales (
    sale_date DATE NOT NULL,
    office_id INTEGER NOT NULL,

    gross_sales NUMERIC(14,2) NOT NULL DEFAULT 0,
    returns_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    net_sales NUMERIC(14,2) NOT NULL DEFAULT 0,

    tickets_count INTEGER NOT NULL DEFAULT 0,
    unresolved_returns_count INTEGER NOT NULL DEFAULT 0,

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (sale_date, office_id),

    CHECK (gross_sales >= 0),
    CHECK (returns_amount >= 0),
    CHECK (tickets_count >= 0),
    CHECK (unresolved_returns_count >= 0)
);

-- 5. Venta diaria por vendedor
CREATE TABLE IF NOT EXISTS public.pacas_seller_daily (
    sale_date DATE NOT NULL,
    office_id INTEGER NOT NULL,

    seller_id BIGINT NOT NULL,
    seller_name TEXT NOT NULL,

    gross_sales NUMERIC(14,2) NOT NULL DEFAULT 0,
    returns_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    net_sales NUMERIC(14,2) NOT NULL DEFAULT 0,

    tickets_count INTEGER NOT NULL DEFAULT 0,

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (sale_date, office_id, seller_id),

    CHECK (gross_sales >= 0),
    CHECK (returns_amount >= 0),
    CHECK (tickets_count >= 0)
);

-- 6. Métodos de pago
CREATE TABLE IF NOT EXISTS public.pacas_payments_daily (
    sale_date DATE NOT NULL,
    office_id INTEGER NOT NULL,

    payment_type_id BIGINT NOT NULL,
    payment_type_name TEXT NOT NULL,
    amount NUMERIC(14,2) NOT NULL DEFAULT 0,

    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (sale_date, office_id, payment_type_id),

    CHECK (amount >= 0)
);

-- 7. Metas mensuales: único dataset manual
CREATE TABLE IF NOT EXISTS public.pacas_monthly_targets (
    period_date DATE NOT NULL,
    office_id INTEGER NOT NULL DEFAULT 6,

    target_amount NUMERIC(14,2) NOT NULL,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (period_date, office_id),

    CHECK (target_amount >= 0),
    CHECK (EXTRACT(DAY FROM period_date) = 1)
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_pacas_documents_date
    ON public.pacas_documents (emission_date);

CREATE INDEX IF NOT EXISTS idx_pacas_documents_type
    ON public.pacas_documents (document_type_id);

CREATE INDEX IF NOT EXISTS idx_pacas_document_sellers_seller
    ON public.pacas_document_sellers (seller_id);

CREATE INDEX IF NOT EXISTS idx_pacas_return_related_detail
    ON public.pacas_return_allocations (related_detail_id);

CREATE INDEX IF NOT EXISTS idx_pacas_seller_daily_date
    ON public.pacas_seller_daily (sale_date);

COMMIT;
