-- 001_initial_schema.sql
-- Bsale Data Pipeline: esquema relacional inicial para Neon/PostgreSQL.

begin;

create table if not exists public.daily_sales (
    sale_date date not null,
    office_id integer not null,
    office_name text not null,

    cash_amount numeric(14, 2) not null default 0,
    card_amount numeric(14, 2) not null default 0,
    flux_amount numeric(14, 2) not null default 0,

    gross_sales numeric(14, 2) not null default 0,
    returns_amount numeric(14, 2) not null default 0,
    net_sales numeric(14, 2) not null default 0,

    tickets_count integer not null default 0,
    pieces_sold integer not null default 0,

    source_updated_at timestamptz,
    synced_at timestamptz not null default now(),

    primary key (sale_date, office_id),

    check (cash_amount >= 0),
    check (card_amount >= 0),
    check (flux_amount >= 0),
    check (gross_sales >= 0),
    check (returns_amount >= 0),
    check (tickets_count >= 0),
    check (pieces_sold >= 0)
);

create table if not exists public.products_daily (
    sale_date date not null,
    office_id integer not null,
    office_name text not null,

    variant_id bigint not null,
    variant_code text,
    variant_description text not null,

    pieces_sold integer not null default 0,
    gross_sales numeric(14, 2) not null default 0,
    returns_amount numeric(14, 2) not null default 0,
    net_sales numeric(14, 2) not null default 0,
    tax_amount numeric(14, 2) not null default 0,

    synced_at timestamptz not null default now(),

    primary key (sale_date, office_id, variant_id),

    check (pieces_sold >= 0),
    check (gross_sales >= 0),
    check (returns_amount >= 0)
);

create table if not exists public.category_product_types (
    product_type_id bigint primary key,
    product_type_name text not null,
    is_active boolean,
    synced_at timestamptz not null default now()
);

create table if not exists public.category_products (
    product_id bigint primary key,
    product_name text not null,
    product_type_id bigint references public.category_product_types(product_type_id),
    is_active boolean,
    synced_at timestamptz not null default now()
);

create table if not exists public.category_variants (
    variant_id bigint primary key,
    variant_code text,
    variant_description text not null,
    product_id bigint references public.category_products(product_id),
    is_active boolean,
    synced_at timestamptz not null default now()
);

create table if not exists public.categories_daily (
    sale_date date not null,
    office_id integer not null,
    office_name text not null,

    category_key text not null,
    category_name text not null,
    resolution_status text not null default 'RESOLVED',

    pieces_sold integer not null default 0,
    gross_sales numeric(14, 2) not null default 0,
    returns_amount numeric(14, 2) not null default 0,
    net_sales numeric(14, 2) not null default 0,
    tax_amount numeric(14, 2) not null default 0,

    synced_at timestamptz not null default now(),

    primary key (sale_date, office_id, category_key),

    check (
        resolution_status in (
            'RESOLVED',
            'MISSING_VARIANT',
            'MISSING_PRODUCT',
            'MISSING_PRODUCT_TYPE'
        )
    ),
    check (pieces_sold >= 0),
    check (gross_sales >= 0),
    check (returns_amount >= 0)
);

create index if not exists idx_products_daily_date_office
    on public.products_daily (sale_date, office_id);

create index if not exists idx_categories_daily_date_office
    on public.categories_daily (sale_date, office_id);

create index if not exists idx_category_variants_product_id
    on public.category_variants (product_id);

create index if not exists idx_category_products_product_type_id
    on public.category_products (product_type_id);

commit;