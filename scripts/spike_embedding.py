"""So sánh multilingual-e5-small vs bge-m3, có/không tiền tố query:/passage:.

Chủ sở hữu: T | Task: 2.6 | xem Task.md

CLIProxy không có endpoint embedding (Plan.md mục 2.6, xác nhận lại ở docs/cliproxy-notes.md
mục 6), nên embedding do service `embedder` cục bộ đảm nhiệm. Script này chọn model cho service
đó và đo bằng số thật thay vì tin vào bảng benchmark trên mạng.

Chạy::

    python scripts/spike_embedding.py                      # chỉ e5-small (mặc định)
    python scripts/spike_embedding.py --models e5 bge      # so cả hai
    python scripts/spike_embedding.py --json ket-qua.json  # xuất số liệu để dán vào ADR

Tiêu chí đạt (Task.md 2.6): top-3 chứa đoạn đúng ở **≥ 8/10** truy vấn **và** < 200ms/đoạn
trên CPU. Đạt → chốt e5-small; trượt → thử bge-m3; cả hai trượt → báo Q chuyển RAG sang
`tsvector`.

Bộ dữ liệu bên dưới cố tình có **nhiều công ty cùng ngành** (3 công ty logistics, 2 công ty
phần mềm) để bài đo không quá dễ: model phải phân biệt được đúng công ty chứ không chỉ đúng
chủ đề.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

MODELS = {
    "e5": "intfloat/multilingual-e5-small",
    "bge": "BAAI/bge-m3",
}

#: 20 đoạn mô tả công ty, đủ 5 ngôn ngữ Anh/Việt/Hàn/Nhật/Trung (4 đoạn mỗi ngôn ngữ).
#: Nội dung mô phỏng đúng thứ sẽ nằm trong Knowledge Base thật: hồ sơ doanh nghiệp đã enrich.
PASSAGES: list[tuple[str, str]] = [
    # --- Tiếng Việt ---
    (
        "vi-logi-1",
        "Công ty Cổ phần Vận tải Trường Hải chuyên vận tải đường bộ Bắc - Nam, sở hữu đội xe container 200 chiếc, trụ sở tại Đà Nẵng.",
    ),
    (
        "vi-logi-2",
        "Công ty TNHH Kho vận Sài Gòn cung cấp dịch vụ kho bãi và giao nhận hàng hoá, hệ thống kho lạnh 15.000 m2 tại Bình Dương.",
    ),
    (
        "vi-soft-1",
        "Công ty Phần mềm Hà Nội phát triển hệ thống quản trị doanh nghiệp ERP cho ngành bán lẻ, quy mô 300 kỹ sư.",
    ),
    (
        "vi-food-1",
        "Công ty Cổ phần Thực phẩm An Khang sản xuất và xuất khẩu nông sản sấy khô sang thị trường Nhật Bản và Hàn Quốc.",
    ),
    # --- Tiếng Anh ---
    (
        "en-logi-1",
        "Pacific Freight Solutions is a third-party logistics provider specialising in sea freight forwarding between Southeast Asia and the US West Coast.",
    ),
    (
        "en-soft-1",
        "Northwind Analytics builds cloud data warehousing and business intelligence dashboards for mid-market insurance companies.",
    ),
    (
        "en-manu-1",
        "Kestrel Precision Components manufactures CNC-machined aluminium parts for the aerospace and medical device industries.",
    ),
    (
        "en-fin-1",
        "Bluewater Capital Partners is a boutique investment advisory firm focused on cross-border mergers and acquisitions in renewable energy.",
    ),
    # --- Tiếng Hàn ---
    (
        "ko-logi-1",
        "한성물류 주식회사는 부산항을 중심으로 컨테이너 운송과 통관 대행 서비스를 제공하는 종합물류기업입니다.",
    ),
    (
        "ko-soft-1",
        "넥스트코드 주식회사는 제조업 고객을 위한 스마트팩토리 MES 솔루션과 산업용 IoT 플랫폼을 개발합니다.",
    ),
    (
        "ko-cosm-1",
        "뷰티라인 주식회사는 천연 원료 기반 스킨케어 화장품을 제조하며 중국과 동남아시아에 수출하고 있습니다.",
    ),
    (
        "ko-cons-1",
        "대한건설 주식회사는 아파트 및 상업용 건물 시공을 전문으로 하는 종합건설업체로 서울에 본사를 두고 있습니다.",
    ),
    # --- Tiếng Nhật ---
    (
        "ja-logi-1",
        "山陽運輸株式会社は関西圏を拠点に、冷蔵・冷凍食品の低温物流サービスを提供する運送会社です。",
    ),
    (
        "ja-soft-1",
        "株式会社テックブリッジは、中小企業向けのクラウド会計ソフトと請求書電子化サービスを開発しています。",
    ),
    (
        "ja-manu-1",
        "旭精密工業株式会社は自動車部品向けの精密金型の設計・製造を行い、愛知県に二つの工場を持っています。",
    ),
    (
        "ja-trade-1",
        "丸協商事株式会社は繊維原料の輸入商社として、インドと東南アジアから綿花や化学繊維を調達しています。",
    ),
    # --- Tiếng Trung ---
    (
        "zh-logi-1",
        "深圳市远洋国际货运代理有限公司主要经营国际海运、空运货代业务，在深圳盐田港设有自营仓库。",
    ),
    ("zh-soft-1", "北京智云科技有限公司专注于企业级人工智能客服系统和自然语言处理平台的研发。"),
    (
        "zh-elec-1",
        "东莞市宏发电子有限公司生产各类连接器和线束产品，主要供应给家电与新能源汽车厂商。",
    ),
    (
        "zh-pharm-1",
        "上海康泰生物制药有限公司从事疫苗与生物制剂的研发生产，拥有两条通过GMP认证的生产线。",
    ),
]

#: 10 truy vấn tự soạn. Cố tình pha ngôn ngữ truy vấn ≠ ngôn ngữ đoạn văn ở vài câu để kiểm
#: khả năng truy hồi xuyên ngôn ngữ — đúng tình huống thật: người Việt hỏi về danh thiếp Nhật.
QUERIES: list[tuple[str, str]] = [
    ("Công ty nào làm về kho lạnh và kho bãi?", "vi-logi-2"),
    ("Doanh nghiệp xuất khẩu nông sản sang Nhật", "vi-food-1"),
    ("Which company does sea freight forwarding to the United States?", "en-logi-1"),
    ("aerospace precision machining supplier", "en-manu-1"),
    ("M&A advisory for renewable energy", "en-fin-1"),
    ("부산항 컨테이너 운송 회사", "ko-logi-1"),
    ("스마트팩토리 MES 개발 업체", "ko-soft-1"),
    ("冷凍食品を運ぶ物流会社はどこですか", "ja-logi-1"),
    ("自動車部品の金型メーカー", "ja-manu-1"),
    ("哪家公司做企业级人工智能客服系统？", "zh-soft-1"),
]


@dataclass
class Result:
    """Số liệu một lần đo (một model × một cách dùng tiền tố)."""

    model: str
    dim: int
    use_prefix: bool
    top1: int
    top3: int
    total_queries: int
    ms_per_passage: float
    ms_model_load: float
    #: Cosine trung bình của đoạn ĐÚNG với truy vấn.
    mean_correct_score: float
    #: Biên trung bình = điểm đoạn đúng − điểm đoạn SAI cao nhất. Biên càng lớn càng khó xếp
    #: nhầm khi Knowledge Base phình to. Đây là chỉ số duy nhất đủ nhạy để thấy tác dụng của
    #: tiền tố khi top-3 đã kịch trần 10/10 — xem docs/adr-embedding.md mục 4.
    mean_margin: float

    @property
    def passed(self) -> bool:
        """Tiêu chí đạt/trượt của task 2.6."""
        return self.top3 >= 8 and self.ms_per_passage < 200


def _load_model(model_name: str) -> tuple[SentenceTransformer, float]:
    from sentence_transformers import SentenceTransformer

    started = time.perf_counter()
    model = SentenceTransformer(model_name, device="cpu")
    return model, (time.perf_counter() - started) * 1000


def run_measurement(key: str, use_prefix: bool) -> Result:
    """Nhúng toàn bộ đoạn văn + truy vấn, đo recall top-1/top-3 và thời gian nhúng."""
    import numpy as np

    model_name = MODELS[key]
    model, ms_load = _load_model(model_name)

    passage_prefix = "passage: " if use_prefix else ""
    query_prefix = "query: " if use_prefix else ""

    texts = [f"{passage_prefix}{content}" for _, content in PASSAGES]
    started = time.perf_counter()
    passage_vecs = model.encode(texts, normalize_embeddings=True, batch_size=8)
    ms_per_passage = (time.perf_counter() - started) * 1000 / len(texts)

    query_vecs = model.encode(
        [f"{query_prefix}{question}" for question, _ in QUERIES],
        normalize_embeddings=True,
        batch_size=8,
    )

    passage_ids = [pid for pid, _ in PASSAGES]
    top1 = top3 = 0
    correct_scores: list[float] = []
    margins: list[float] = []
    for i, (question, expected_id) in enumerate(QUERIES):
        # Vector đã chuẩn hoá L2 → tích vô hướng chính là cosine similarity, khớp với
        # index ivfflat (cosine) đã thiết kế ở docs/erd.md mục 3.
        scores = passage_vecs @ query_vecs[i]
        ranked = [passage_ids[j] for j in np.argsort(-scores)[:3]]
        if ranked[0] == expected_id:
            top1 += 1
        if expected_id in ranked:
            top3 += 1
        else:
            print(f"    trượt: {question[:45]!r} → mong {expected_id}, nhận {ranked}")

        expected_idx = passage_ids.index(expected_id)
        correct = float(scores[expected_idx])
        best_wrong = max(float(s) for j, s in enumerate(scores) if j != expected_idx)
        correct_scores.append(correct)
        margins.append(correct - best_wrong)

    return Result(
        model=model_name,
        # Lấy số chiều từ chính vector vừa sinh: luôn đúng, và tránh
        # `get_sentence_embedding_dimension()` vốn đã bị đánh dấu deprecated.
        dim=int(passage_vecs.shape[1]),
        use_prefix=use_prefix,
        top1=top1,
        top3=top3,
        total_queries=len(QUERIES),
        ms_per_passage=round(ms_per_passage, 1),
        ms_model_load=round(ms_load, 1),
        mean_correct_score=round(sum(correct_scores) / len(correct_scores), 4),
        mean_margin=round(sum(margins) / len(margins), 4),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike chọn model embedding (task 2.6)")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=sorted(MODELS),
        default=["e5"],
        help="Model cần đo. Mặc định chỉ e5; thêm 'bge' để tải BAAI/bge-m3 (~4.3GB).",
    )
    parser.add_argument("--json", help="Ghi số liệu ra file JSON để dán vào ADR")
    args = parser.parse_args()

    results: list[Result] = []
    for key in args.models:
        for use_prefix in (True, False):
            label = "có tiền tố" if use_prefix else "không tiền tố"
            print(f"\n=== {MODELS[key]} ({label}) ===")
            result = run_measurement(key, use_prefix)
            results.append(result)
            print(
                f"  top-1 {result.top1}/{result.total_queries}"
                f" · top-3 {result.top3}/{result.total_queries}"
                f" · biên {result.mean_margin:+.4f} · {result.ms_per_passage}ms/đoạn"
                f" · dim={result.dim} · {'ĐẠT' if result.passed else 'TRƯỢT'}"
            )

    print("\n" + "=" * 92)
    print(
        f"{'Model':<34}{'Tiền tố':<10}{'top-1':<8}{'top-3':<8}"
        f"{'điểm đúng':<12}{'biên':<10}{'ms/đoạn':<10}{'Kết luận'}"
    )
    print("-" * 92)
    for result in results:
        print(
            f"{result.model:<34}{'có' if result.use_prefix else 'không':<10}"
            f"{result.top1}/{result.total_queries:<6}{result.top3}/{result.total_queries:<6}"
            f"{result.mean_correct_score:<12.4f}{result.mean_margin:<10.4f}"
            f"{result.ms_per_passage:<10}{'ĐẠT' if result.passed else 'TRƯỢT'}"
        )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
        print(f"\nĐã ghi số liệu vào {args.json}")


if __name__ == "__main__":
    main()
