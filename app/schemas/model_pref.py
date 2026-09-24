"""Schema cho `GET` / `PUT /api/integration/models` — chọn model theo từng chức năng.

Chủ sở hữu: Q | Task: EX-15 | xem `docs/adr-model-per-feature.md`
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeatureModelsOut(BaseModel):
    """Một chức năng: đang chọn gì, chọn được những gì."""

    key: str = Field(description="`ocr` · `enrich` · `chat`")
    label: str
    hint: str = Field(description="Vì sao danh sách của chức năng này dài/ngắn như vậy.")
    selected: str | None = Field(
        default=None,
        description="`null` = dùng model mặc định của hệ thống (`LLM_MODEL`), không phải chưa chọn.",
    )
    selected_available: bool = Field(
        default=True,
        description="Model đã lưu còn nằm trong danh sách cho phép không. `false` = nó đã biến "
        "mất khỏi danh mục hoặc bị bảng năng lực loại — lời gọi sẽ **rơi về mặc định** chứ không "
        "hỏng (ADR mục 4, M5), nhưng giao diện phải nói ra.",
    )
    available: list[str] = Field(
        default_factory=list,
        description="Danh mục thật của channel đã lọc theo năng lực đo được của chức năng này.",
    )


class ModelPrefsOut(BaseModel):
    """Trạng thái đầy đủ của khối *Model cho từng chức năng* trên `/settings`."""

    default_model: str = Field(description="`LLM_MODEL` — thứ dùng khi một chức năng để trống.")
    reachable: bool = Field(
        default=True,
        description="Có hỏi được danh mục từ CLIProxy không. `false` thì `available` rỗng và "
        "**không đổi được lựa chọn** — không có gì để đối chiếu thì lưu gì cũng là lưu mò.",
    )
    features: list[FeatureModelsOut] = Field(default_factory=list)


class ModelPrefsIn(BaseModel):
    """Body của `PUT`. **Chức năng không nhắc tới thì giữ nguyên.**

    Ba trường đều `None` được, và `None` ở đây nghĩa là *"về dùng mặc định"* — khác hẳn *"không
    gửi trường này"*. Hai thứ đó phân biệt bằng `model_fields_set` chứ không bằng giá trị, nên
    gửi `{"ocr": null}` là xoá lựa chọn cho quét danh thiếp, còn gửi `{}` là không đổi gì cả.
    """

    ocr: str | None = None
    enrich: str | None = None
    chat: str | None = None
