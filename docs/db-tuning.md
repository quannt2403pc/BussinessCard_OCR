# Rà soát truy vấn F2 và index đề xuất

> Chủ sở hữu: **T** · Task: **9.8** · Người dùng kết quả: **Q** (migration ở task 9.2) · Đo ngày 2026-09-18

## 1. Kết luận

| # | Đề xuất | Bảng | Làm gì | Lợi ích đo được |
|---|---------|------|--------|-----------------|
| 1 | **Thêm** `ix_business_cards_uploaded_at_id` | `business_cards (uploaded_at, id)` | Q sinh migration + khai trong `models/card.py` | Danh sách thẻ trang 1: 2.46 → 0.02 ms · Export thẻ mỗi lô: 6.62 → 0.35 ms |
| 2 | **Thêm** `ix_companies_display_name_id` | `companies (display_name, id)` | Q sinh migration; dòng khai trong `models/company.py` (file của T) — xem mục 5 | Danh sách công ty trang 1: 2.13 → 0.11 ms · Export công ty mỗi lô: 5.18 → 0.76 ms |
| 3 | **Không thêm** index trigram cho tìm kiếm | `companies` | — | Planner **không dùng** kể cả khi đã tạo (mục 4) |
| 4 | Ghi nhận: `ix_company_profiles_industry` (GIN) chưa có truy vấn nào dùng | `company_profiles` | Không cần migration riêng để xoá | — |

**Không có N+1** ở phía F2: `contact_count` là subquery tương quan trong cùng một câu, trang chi tiết 3 câu cố định,
`GET /api/stats` 1 câu, export đọc theo lô 200.

**Nói thẳng về quy mô**: dữ liệu demo chỉ vài trăm dòng. Ở quy mô đó mọi truy vấn dưới 1 ms và **không index nào tạo
khác biệt người dùng cảm nhận được**. Hai index đề xuất là việc rẻ và an toàn (btree thường, không cần extension, không
đổi code), nhưng ưu tiên thấp. Nếu 9.2 tràn giờ thì bỏ đề xuất này trước mọi thứ khác.

## 2. Cách đo

```bash
PYTHONPATH=. python scripts/spike_db_tuning.py
```

Script tạo database riêng `bizcard_tuning` (không đụng DB đang dùng, xoá sau khi chạy, `--keep` để giữ lại), dựng
schema từ `Base.metadata`, sinh **20 000 danh thiếp + 3 000 công ty** (một nửa có hồ sơ, 1/3 đã qua một lượt enrich,
cứ 10 thẻ chung một mốc `uploaded_at` như một lượt upload hàng loạt), rồi chạy `EXPLAIN (ANALYZE)` 7 lượt, lấy trung
vị, **trước và sau** khi tạo index ứng viên.

Câu SQL được dựng **bằng chính hàm của code thật** (`company_repo._company_rows`, `_list_conditions`,
`export._cards_select`, `export._companies_select`) chứ không chép tay, nên đo đúng thứ đang chạy. Cần Postgres đang
chạy (`docker compose up -d db`). Đổi quy mô bằng `--cards` / `--companies`.

## 3. Số đo

Postgres 16 (`pgvector/pgvector:pg16`) trong Docker Desktop, máy dev Windows.

| Truy vấn | Hiện tại (ms) | Có index (ms) | Plan hiện tại | Plan có index |
|----------|--------------:|--------------:|---------------|---------------|
| companies: page 1 | 2.13 | 0.11 | Sort → Seq Scan companies | Index Scan `ix_companies_display_name_id` |
| companies: page 50 | 14.07 | 14.84 | Sort → Seq Scan companies | *(không đổi)* |
| companies: q=logistics | 3.94 | 1.09 | Sort → Seq Scan | Index Scan `ix_companies_display_name_id` + lọc |
| companies: count q=logistics | 3.30 | 3.17 | Seq Scan companies | Seq Scan companies |
| companies: has_profile=true | 1.33 | 0.16 | Sort → Seq Scan | Index Scan `ix_companies_display_name_id` |
| company detail: contacts | 0.02 | 0.02 | Index Scan `ix_business_cards_company_id` → Sort | *(không đổi)* |
| enrich: active item of a company | 0.01 | 0.01 | Index Scan `uq_enrich_job_items_active_company` | *(không đổi)* |
| export cards: batch mid-table | 6.62 | 0.35 | Sort → Seq Scan business_cards | Index Scan `ix_business_cards_uploaded_at_id` |
| export companies: batch mid-table | 5.18 | 0.76 | Sort → Seq Scan companies | Index Scan `ix_companies_display_name_id` |
| cards list (Q): page 1 | 2.46 | 0.02 | Sort → Seq Scan business_cards | Index Scan `ix_business_cards_uploaded_at_id` |

Đọc bảng:

- **Hai index btree gánh toàn bộ phần cải thiện.** Mọi truy vấn có `ORDER BY … LIMIT` theo đúng cặp cột của index đều
  bỏ được bước sắp xếp cả bảng.
- **Một index phục vụ cả F1 lẫn F2**: `GET /api/cards` của Q sắp xếp `uploaded_at DESC, id DESC`, export 7.6 duyệt
  keyset `(uploaded_at, id)` tăng dần. Postgres quét btree theo hai chiều, nên một index đủ cho cả hai.
- **Trang 50 không đổi**: với `OFFSET 980` trên 3 000 dòng, planner chọn quét + sắp xếp thay vì đi index gần hết
  bảng. Đúng hành vi, không phải lỗi; ở quy mô demo không ai lật tới trang 50.
- **Đếm tổng khi tìm kiếm luôn quét cả bảng** (xem mục 4).
- Các chỗ đã có index đúng từ trước — người liên hệ của một công ty, job enrich đang chạy của một công ty — vẫn dưới
  0.05 ms. Partial unique index của 5.8 vừa chống trùng vừa là đường tìm nhanh.

## 4. Vì sao không đề xuất index trigram

Tìm kiếm công ty (`_list_conditions`) là `OR` của ba điều kiện `ILIKE '%…%'`: `display_name`, `name_normalized`
và `array_to_string(aliases, ' ')`. Btree không dùng được cho `%…%`; về lý thuyết `pg_trgm` + GIN thì dùng được.

Đã tạo thử `ix_companies_search_trgm` (GIN trên `display_name` và `name_normalized`), rồi **planner không chọn nó ở
truy vấn nào**. Lý do: nhánh thứ ba không đánh index được — `array_to_string` là hàm `STABLE`, không phải `IMMUTABLE`,
nên Postgres không cho tạo index biểu thức trên nó. `OR` có một nhánh không có index thì Postgres phải quét cả bảng.

Muốn trigram có tác dụng thì phải đổi cả truy vấn lẫn schema (thêm cột tên-khác dạng text, hoặc tách truy vấn thành
`UNION`), cộng thêm việc bật extension. Không đáng với quy mô demo.

## 5. Việc cho Q (task 9.2)

Một revision duy nhất, gộp được với các thay đổi schema khác của 9.2:

```python
def upgrade() -> None:
    op.create_index(
        "ix_business_cards_uploaded_at_id", "business_cards", ["uploaded_at", "id"]
    )
    op.create_index("ix_companies_display_name_id", "companies", ["display_name", "id"])


def downgrade() -> None:
    op.drop_index("ix_companies_display_name_id", table_name="companies")
    op.drop_index("ix_business_cards_uploaded_at_id", table_name="business_cards")
```

Để `alembic check` không báo lệch, model phải khai **cùng lúc** với migration:

- `models/card.py` (file của Q): `Index("ix_business_cards_uploaded_at_id", "uploaded_at", "id")` trong
  `__table_args__`.
- `models/company.py` (file của **T**): `Index("ix_companies_display_name_id", "display_name", "id")` trong
  `__table_args__` của `Company`. Đề xuất: Q thêm đúng dòng này trong cùng PR migration (T xác nhận khi review PR),
  tránh phải chia hai PR cho một thay đổi. Chưa khai trước vì khai model mà chưa có migration thì `alembic check` báo
  lệch ngay.

Không đề xuất xoá `ix_company_profiles_industry`: nó chưa có truy vấn nào dùng nhưng là thiết kế ghi trong `Plan.md`
mục 3 (lọc theo ngành), và một migration chỉ để xoá nó không đem lại gì ở quy mô demo.
