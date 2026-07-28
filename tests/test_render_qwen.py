from piemgmaker.workflows.render import (
    compose,
    fill_slots,
    load_fragment,
    load_template,
    strip_meta,
    unfilled_slots,
)

QWEN_SLOTS = {
    "model": "Qwen_Image-Q8_0.gguf",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "positive": "a bag",
    "negative": "text",
    "width": 1024,
    "height": 1024,
    "steps": 20,
    "cfg": 2.5,
    "prefix": "pm_test",
}


def test_qwen_템플릿은_분리_로더_슬롯까지_채워진다():
    graph, ref = load_template("object-gen-qwen-v1")
    filled = fill_slots(graph, QWEN_SLOTS)
    assert filled["1"]["inputs"]["unet_name"] == "Qwen_Image-Q8_0.gguf"
    assert filled["2"]["inputs"]["type"] == "qwen_image"
    assert unfilled_slots(filled) == set()
    assert ref.id == "object-gen-qwen-v1"


def test_qwen_인페인트_템플릿은_입력_이미지_토큰만_남긴다():
    graph, _ = load_template("object-inpaint-qwen-v1")
    filled = fill_slots(graph, {**QWEN_SLOTS, "denoise": 0.85}, allow_missing=True)
    assert unfilled_slots(filled) == {"__CANVAS__", "__MASK_IMAGE__"}


def test_trimap_조각은_이미지를_두_노드에_배선한다():
    graph, _ = load_template("object-gen-qwen-v1")
    fragment = load_fragment("matting_trimap")
    merged = compose(graph, fragment)
    # BiRefNet(코스 마스크)과 ApplyMatting 둘 다 베이스 이미지 출력("8")을 받는다
    assert merged["mat_F1"]["inputs"]["image"] == ["8", 0]
    assert merged["mat_F4"]["inputs"]["image"] == ["8", 0]
    # SaveImage는 RGBA 합성 노드(F6) 출력으로 재배선
    assert merged["9"]["inputs"]["images"] == ["mat_F6", 0]
    assert "__meta__" not in strip_meta(merged)
