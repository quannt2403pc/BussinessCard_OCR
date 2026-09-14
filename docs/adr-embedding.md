# ADR — Chọn model embedding cho RAG

> Chủ sở hữu: **T** · Task **2.6** · Ngày: **2026-09-10** · Trạng thái: **Đã chốt**
> Script đo: [`scripts/spike_embedding.py`](../scripts/spike_embedding.py)
> Bối cảnh kiến trúc: [Plan.md](../Plan.md) mục 2.6 · Xác nhận CLIProxy không có endpoint
> embedding: [`docs/cliproxy-notes.md`](./cliproxy-notes.md) mục 6

---

## 1. Quyết định

**Chốt `intfloat/multilingual-e5-small`, 384 chiều, có dùng tiền tố `query:` / `passage:`.**

384 chiều **khớp đúng** cột `kb_chunks.embedding = vector(384)` đã tạo ở migration `0001`
→ **không cần Q sinh Alembic revision**. Điều kiện "báo Q ngay trong ngày nếu chốt model khác
384 chiều" (Task.md 2.6) không phát sinh.

---

## 2. Cách đo

| Hạng mục | Giá trị |
|---|---|
| Bộ đoạn văn | 20 mô tả công ty, 4 đoạn × 5 ngôn ngữ (Anh/Việt/Hàn/Nhật/Trung) |
| Bộ truy vấn | 10 câu tự soạn, một số cố tình khác ngôn ngữ với đoạn đích (truy hồi xuyên ngôn ngữ) |
| Độ khó | Cố ý có 3 công ty logistics + 2 công ty phần mềm để model phải phân biệt **đúng công ty**, không chỉ đúng chủ đề |
| Phần cứng | CPU, Windows 11, chạy trong `.venv` (Python 3.12.10, torch 2.14.0+cpu, sentence-transformers 6.0.1) |
| Chuẩn hoá | `normalize_embeddings=True` (L2) → tích vô hướng = cosine, khớp index `ivfflat` ở `docs/erd.md` mục 3 |
| Tiêu chí đạt | top-3 chứa đoạn đúng ở ≥ 8/10 truy vấn **và** < 200ms/đoạn |

Ngoài top-k, script đo thêm **biên** = `điểm(đoạn đúng) − điểm(đoạn sai cao nhất)`. Lý do ở mục 4.

### Chạy lại số liệu này

`torch` và `sentence-transformers` **cố ý không nằm trong `requirements.txt`** — chúng chỉ
cần cho spike và cho image `embedder` (build trong Docker), không cần cho `api`. Sau khi chốt
model, chúng đã được gỡ khỏi `.venv` để không chiếm ~820MB vô ích. Muốn chạy lại:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install sentence-transformers
python scripts/spike_embedding.py --models e5 bge   # bge sẽ tải lại ~4.3GB
```

Model `bge-m3` cũng đã xoá khỏi cache HuggingFace sau khi chốt (4.3GB). `e5-small` (471MB)
giữ lại vì là model đã chọn.

---

## 3. Kết quả

| Model | Tiền tố | top-1 | top-3 | Điểm đoạn đúng | **Biên** | ms/đoạn | Kết luận |
|---|---|---|---|---|---|---|---|
| **multilingual-e5-small** (384d) | **có** | 10/10 | 10/10 | 0.8809 | **+0.0734** | **12.7** | ✅ ĐẠT |
| multilingual-e5-small (384d) | không | 10/10 | 10/10 | 0.8871 | +0.0688 | 11.8 | ✅ ĐẠT |
| BAAI/bge-m3 (1024d) | có | 10/10 | 10/10 | 0.6456 | +0.0992 | 1523.0 ⚠️ | (xem ghi chú) |
| BAAI/bge-m3 (1024d) | không | 10/10 | 10/10 | 0.6255 | **+0.1458** | 153.3 | ✅ ĐẠT |

> ⚠️ **Con số 1523ms của bge-m3 là ảo — do warm-up lần gọi đầu tiên trong tiến trình, không
> phải tốc độ thật.** Lần đo thứ hai cùng model ra 153.3ms (~10×). **Đừng trích con số 1523ms
> đi đâu.** Tốc độ thật của bge-m3 trên máy này là **~153ms/đoạn**, tức vẫn đạt ngưỡng 200ms
> nhưng sát trần.

> **Về độ tin cậy của cột `ms/đoạn`:** chạy lại nhiều lần cho e5-small ra 31.6 / 12.7 / 7.7
> ms/đoạn — dao động hơn 4× tuỳ trạng thái máy và thứ tự nạp model. Cột này chỉ nên đọc ở mức
> **bậc độ lớn** (e5 ~10ms, bge ~150ms), đừng so hơn kém vài ms. Ngược lại, cột **biên lặp lại
> chính xác đến 4 chữ số thập phân** qua các lần chạy — đó mới là số đáng tin để so sánh.

| Dung lượng thực đo (cache HuggingFace) | |
|---|---|
| `multilingual-e5-small` | **471 MB** (Plan.md ước ~470MB — khớp) |
| `bge-m3` | **4.3 GB** (Plan.md ước ~2.2GB — **thấp hơn thực tế gần 2×**) |

---

## 4. Vì sao phải đo "biên" chứ không chỉ top-3

Cả 4 cấu hình đều đạt **10/10 top-3**. Nếu chỉ nhìn top-k thì kết luận sẽ là *"tiền tố không
có tác dụng gì"* — sai, và sai theo hướng nguy hiểm vì Q sẽ bỏ tiền tố ở task 6.4.

Bộ dữ liệu 20 đoạn quá nhỏ để top-k phân biệt được. **Biên** thì nhạy hơn nhiều và cho thấy
hai điều mà top-k giấu mất:

1. **Với e5, tiền tố có giúp**: biên +0.0734 so với +0.0688 (tốt hơn ~6.7% tương đối).
   Đáng chú ý: điểm tuyệt đối của đoạn đúng lại *thấp hơn* khi có tiền tố (0.8809 vs 0.8871) —
   cái quyết định chất lượng truy hồi là **khoảng cách với đoạn sai**, không phải điểm tuyệt đối.
2. **Với bge-m3, tiền tố làm HẠI**: biên +0.0992 so với +0.1458 khi không tiền tố (**tệ đi 32%**).
   Hợp lý — `query:` / `passage:` là quy ước riêng của họ e5, bge-m3 không được huấn luyện với
   chúng nên thêm vào chỉ là nhiễu.

→ **Tiền tố gắn liền với model, không phải quy tắc chung.** Đổi model sang họ khác thì phải
xem lại có dùng tiền tố hay không.

### Đính chính một khẳng định trong Plan.md

Plan.md mục 2.6 viết: *"Quên bước này thì chất lượng truy hồi **tụt rõ rệt** — đây là lỗi âm
thầm"*. Ở quy mô demo này, **không đo được sự "tụt rõ rệt" đó**: top-3 vẫn 10/10 khi bỏ tiền
tố, chỉ biên hẹp lại chút ít.

Vẫn **giữ tiền tố** vì nó đúng hướng, không tốn gì, và tác dụng có thể lớn hơn khi KB phình to.
Nhưng Q nên biết: nếu truy hồi có vấn đề ở D7, **tiền tố không phải nghi phạm đầu tiên** — đừng
mất thời gian debug ở đó.

---

## 5. Vì sao không chọn bge-m3 dù biên tốt hơn

bge-m3 tách đoạn đúng/sai tốt hơn thật (biên +0.1458 so với +0.0734, gấp ~2×). Nhưng:

| Tiêu chí | e5-small | bge-m3 | Chênh |
|---|---|---|---|
| Tốc độ nhúng | 12.7 ms/đoạn | ~153 ms/đoạn | **chậm hơn 12×** |
| Dung lượng model | 471 MB | 4.3 GB | **nặng hơn 9×** |
| Số chiều | 384 | 1024 | index `ivfflat` nặng hơn 2.7× |
| Migration | khớp `vector(384)` sẵn có | **phải nhờ Q sinh revision** đổi kiểu cột trước D6 | thêm việc cho người khác |
| Biên độ an toàn với ngưỡng 200ms | 12.7 / 200 = **6%** | 153 / 200 = **77%** | sát trần |

Ba lý do quyết định:

1. **Độ chính xác đã kịch trần với cả hai** (10/10). Biên tốt hơn không mua thêm được câu trả
   lời đúng nào ở quy mô demo (dự kiến vài chục đến vài trăm chunk).
2. **153ms/đoạn là sát trần nguy hiểm.** Đoạn đo ở đây ngắn (1 câu). Hồ sơ doanh nghiệp thật
   dài hơn nhiều → chậm hơn nữa. Reindex toàn bộ KB (task 6.4) sẽ lâu thấy rõ.
3. **4.3GB nhúng vào image `embedder`** làm `docker build` và `docker compose up` trên máy sạch
   chậm hẳn — đụng trực tiếp tiêu chí nghiệm thu A1.

**Giữ bge-m3 làm phương án dự phòng**: nếu D7 đo recall thấy e5-small không đủ, đổi sang bge-m3
chỉ cần sửa `EMBEDDING_MODEL` + nhờ Q sinh revision đổi `vector(384)` → `vector(1024)`.
Chạy lại số liệu bằng `python scripts/spike_embedding.py --models e5 bge`.

---

## 6. Chốt cho các task phía sau

| Task | Người | Phải làm gì với kết quả này |
|---|---|---|
| **3.10** | T | `embedder/` dùng `EMBEDDING_MODEL=intfloat/multilingual-e5-small`, **tải model lúc `docker build`** (không tải lúc chạy). **Embedder tự thêm tiền tố `passage: `/`query: ` theo trường `kind`** của `POST /embed`, và bỏ tiền tố có sẵn trước khi thêm để không bị lặp. Dự trù ~471MB model + torch CPU trong image |
| **6.4** | Q | `services/embeddings.py`: gửi `kind: "passage"` khi index, `kind: "query"` khi truy vấn — **không tự thêm tiền tố** (embedder đã thêm). Vector trả về đã chuẩn hoá L2, so bằng cosine |

> **Chốt 2026-09-14 — tiền tố đặt trong embedder, không đặt ở `embeddings.py`.** Mục 4 cho thấy tiền tố
> gắn liền với model (có lợi cho e5, làm hại bge-m3), nên để cạnh model: đổi model thì chỉ sửa một chỗ.
> Hợp đồng `POST /embed` ở Plan.md 2.6 vốn đã có trường `kind` cho việc này.
| **6.3** | Q | Index `ivfflat` cosine trên `vector(384)` — **không cần đổi migration**, `0001` đã đúng |
| **7.4** | Q | Nếu recall thấp: xem mục 4, tiền tố không phải nghi phạm chính. Cân nhắc bge-m3 theo mục 5 |

`EMBEDDING_MODEL` và `EMBEDDING_DIM=384` trong `.env.example` / `docker-compose.yml` của Q
**đã đúng sẵn từ D1**, không phải sửa gì.

---

## 7. Tự đánh giá độ tin cậy của kết luận

Để người đọc sau biết tin đến đâu:

- **Tin được**: e5-small chạy nhanh (12.7ms), nhẹ (471MB), 384 chiều khớp schema. Đây là số đo
  trực tiếp, lặp lại hai lần cho cùng kết quả.
- **Tin vừa phải**: kết luận "e5-small đủ chính xác". 10/10 trên bộ 20 đoạn / 10 truy vấn là
  quá dễ — chưa chạm giới hạn của model. Phải đo lại nghiêm túc ở **task 7.4** khi KB có dữ
  liệu thật.
- **Chưa kiểm chứng**: hành vi khi KB lên vài nghìn chunk. Biên +0.0734 của e5-small hẹp hơn
  bge-m3 đáng kể; nếu KB phình to thì đây là chỗ vỡ trước tiên.
