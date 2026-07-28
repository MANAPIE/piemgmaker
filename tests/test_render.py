import pytest

from piemgmaker.workflows.render import (
    META_KEY,
    RenderError,
    compose,
    fill_slots,
    inject_seed,
    load_fragment,
    load_template,
    strip_meta,
    unfilled_slots,
)

GEN_SLOTS = {
    "model": "test.safetensors",
    "positive": "a bag",
    "negative": "text",
    "width": 1024,
    "height": 1024,
    "steps": 28,
    "cfg": 4.5,
    "prefix": "pm_test",
}


def test_템플릿_2종이_로드되고_해시가_핀된다():
    for workflow_id in ("object-gen-v1", "object-inpaint-v1"):
        graph, ref = load_template(workflow_id)
        assert ref.id == workflow_id
        assert len(ref.hash) == 64
        assert META_KEY in graph


def test_없는_템플릿은_명시적으로_실패한다():
    with pytest.raises(RenderError):
        load_template("object-unknown-v9")


def test_슬롯_주입은_타입을_보존하고_잔여_토큰을_거부한다():
    graph, _ = load_template("object-gen-v1")
    filled = fill_slots(graph, GEN_SLOTS)
    assert filled["4"]["inputs"]["width"] == 1024  # int 그대로
    assert filled["2"]["inputs"]["text"] == "a bag"
    assert unfilled_slots(filled) == set()

    with pytest.raises(RenderError):
        fill_slots(graph, {"model": "m"})  # 나머지 슬롯 누락


def test_allow_missing이면_토큰을_남기고_이후_확인이_가능하다():
    graph, _ = load_template("object-inpaint-v1")
    filled = fill_slots(graph, {**GEN_SLOTS, "denoise": 0.85}, allow_missing=True)
    assert unfilled_slots(filled) == {"__CANVAS__", "__MASK_IMAGE__"}


def test_매팅_조각_합성은_이미지_출력을_가로채_저장에_연결한다():
    graph, _ = load_template("object-gen-v1")
    fragment = load_fragment("matting_segment")
    merged = compose(graph, fragment)
    # 조각 입력 ← 베이스 이미지 출력(VAEDecode "7")
    assert merged["mat_F1"]["inputs"]["image"] == ["7", 0]
    # SaveImage ← 조각 출력
    assert merged["8"]["inputs"]["images"] == ["mat_F1", 0]
    stripped = strip_meta(merged)
    assert META_KEY not in stripped


def test_메타_없는_그래프는_합성할_수_없다():
    with pytest.raises(RenderError):
        compose({"1": {}}, load_fragment("matting_segment"))


def test_시드는_KSampler류에만_주입된다():
    graph, _ = load_template("object-gen-v1")
    seeded = inject_seed(strip_meta(fill_slots(graph, GEN_SLOTS)), 12345)
    assert seeded["5"]["inputs"]["seed"] == 12345

    with pytest.raises(RenderError):
        inject_seed({"1": {"class_type": "SaveImage", "inputs": {}}}, 1)
