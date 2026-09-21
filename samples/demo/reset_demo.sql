-- Xoá dữ liệu do bộ thẻ demo sinh ra để chạy lại buổi demo từ đầu (task 11.7).
-- docker compose exec -T db psql -U bizcard -d bizcard < samples/demo/reset_demo.sql
-- Chỉ xoá thẻ có email demo và 6 công ty demo (theo khoá chuẩn hoá); dữ liệu khác giữ nguyên.
-- Lưu ý: nếu đã quét thẻ THẬT của Vinamilk / Hòa Phát / Coteccons / Samsung / Hitachi thì công ty đó cũng bị xoá.

BEGIN;

CREATE TEMP TABLE demo_companies ON COMMIT DROP AS
SELECT id FROM companies
WHERE name_normalized IN (
    'sua viet nam', 'hoa phat', 'coteccons construction',
    'samsung electronics vietnam', '日立製作所', '삼성전자'
);

CREATE TEMP TABLE demo_cards ON COMMIT DROP AS
SELECT id FROM business_cards
WHERE lower(email) IN (
    'khoa.tran@vinamilk.example.com', 'ha.le@hoaphat.example.com',
    'anh.nguyen@coteccons.example.com', 'jihoon.park@samsung.example.com',
    'bao.pham@hoaphat.example.com', 'hanako.yamada@hitachi.example.com',
    'minsu.kim@samsung.example.com'
)
OR company_id IN (SELECT id FROM demo_companies);

DELETE FROM kb_chunks
WHERE source_id IN (SELECT id FROM demo_cards)
   OR source_id IN (SELECT id FROM demo_companies);

DELETE FROM business_cards WHERE id IN (SELECT id FROM demo_cards);

DELETE FROM companies WHERE id IN (SELECT id FROM demo_companies);

COMMIT;
