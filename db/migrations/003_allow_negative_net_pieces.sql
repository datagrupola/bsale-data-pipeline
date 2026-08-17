-- Permite piezas netas negativas cuando una devolución corresponde
-- a una venta realizada en una fecha anterior.

begin;

alter table public.products_daily
    drop constraint if exists products_daily_pieces_sold_check;

alter table public.categories_daily
    drop constraint if exists categories_daily_pieces_sold_check;

alter table public.daily_sales
    drop constraint if exists daily_sales_pieces_sold_check;

commit;
