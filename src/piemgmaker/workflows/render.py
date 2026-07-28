"""워크플로우 템플릿 렌더러 — 슬롯 주입 · 매팅 조각 합성 · 시드 주입.

템플릿은 ComfyUI API 포맷 JSON. `__NAME__` 문자열이 슬롯이며, `__meta__` 키가
합성 배선 정보를 담는다(제출 전 반드시 제거). 조각(fragment)은 배경 제거 GPU
노드 서브그래프로, 베이스 그래프의 이미지 출력에 연결되고 SaveImage를 가로챈다.
"""

import copy
import hashlib
import json
import re
from pathlib import Path

from piemgmaker.schemas.generation import WorkflowRef

META_KEY = "__meta__"
TOKEN_RE = re.compile(r"^__[A-Z][A-Z0-9_]*__$")

TEMPLATES_DIR = Path(__file__).parent
FRAGMENTS_DIR = TEMPLATES_DIR / "fragments"

# 후보별 시드 주입 대상 — class_type 기준이라 노드 번호와 무관
SEED_INPUTS = {
    "KSampler": "seed",
    "KSamplerAdvanced": "noise_seed",
    "RandomNoise": "noise_seed",
}


class RenderError(ValueError):
    pass


def _template_path(directory: Path, workflow_id: str) -> Path:
    return directory / f"{workflow_id.replace('-', '_')}.json"


def load_template(workflow_id: str, directory: Path | None = None) -> tuple[dict, WorkflowRef]:
    path = _template_path(directory or TEMPLATES_DIR, workflow_id)
    if not path.is_file():
        raise RenderError(f"워크플로우 템플릿 없음: {path}")
    raw = path.read_bytes()
    graph = json.loads(raw)
    return graph, WorkflowRef(id=workflow_id, hash=hashlib.sha256(raw).hexdigest())


def load_fragment(name: str, directory: Path | None = None) -> dict:
    path = (directory or FRAGMENTS_DIR) / f"{name}.json"
    if not path.is_file():
        raise RenderError(f"매팅 조각 없음: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _slot_name(token: str) -> str:
    return token.strip("_").lower()


def fill_slots(graph: dict, slots: dict[str, object], allow_missing: bool = False) -> dict:
    """`__NAME__` 토큰을 슬롯 값으로 치환. allow_missing=False면 잔여 토큰은 오류."""

    def convert(value: object) -> object:
        if isinstance(value, str) and TOKEN_RE.fullmatch(value):
            name = _slot_name(value)
            if name in slots:
                return slots[name]
            if allow_missing:
                return value
            raise RenderError(f"슬롯 값 누락: {value}")
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    return convert(copy.deepcopy(graph))  # type: ignore[return-value]


def unfilled_slots(graph: dict) -> set[str]:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str) and TOKEN_RE.fullmatch(value):
            found.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(graph)
    return found


def compose(base: dict, fragment: dict, prefix: str = "mat") -> dict:
    """베이스 그래프의 이미지 출력에 매팅 조각을 연결하고 SaveImage를 조각 출력으로 재배선."""
    base = copy.deepcopy(base)
    fragment = copy.deepcopy(fragment)
    base_meta = base.pop(META_KEY, None)
    frag_meta = fragment.pop(META_KEY, None)
    if not base_meta or not frag_meta:
        raise RenderError("합성에는 베이스·조각 모두 __meta__가 필요합니다")

    image_out = base_meta["image_out"]
    save_node = base_meta["save_node"]
    mapping = {old: f"{prefix}_{old}" for old in fragment}
    collisions = set(mapping.values()) & set(base.keys())
    if collisions:
        raise RenderError(f"조각 노드 id 충돌: {sorted(collisions)}")

    merged = dict(base)
    for old, node in fragment.items():
        node = copy.deepcopy(node)
        for key, value in list(node.get("inputs", {}).items()):
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in mapping
            ):
                node["inputs"][key] = [mapping[value[0]], value[1]]
        merged[mapping[old]] = node

    # 이미지 입력이 여러 노드에 필요한 조각(예: trimap 체인) 지원
    if "input_nodes" in frag_meta:
        input_points = [(p["node"], p.get("socket", "image")) for p in frag_meta["input_nodes"]]
    else:
        input_points = [(frag_meta["input_node"], frag_meta.get("input_socket", "image"))]
    for node_name, socket in input_points:
        merged[mapping[node_name]].setdefault("inputs", {})[socket] = [image_out, 0]

    output_node = mapping[frag_meta["output_node"]]
    output_socket = frag_meta.get("output_socket", 0)
    merged[save_node]["inputs"]["images"] = [output_node, output_socket]

    # 합성 후에도 저장 노드 참조가 유효하도록 메타를 갱신해 유지 (제출 전 strip_meta 필수)
    merged[META_KEY] = {"image_out": output_node, "save_node": save_node}
    return merged


def strip_meta(graph: dict) -> dict:
    graph = copy.deepcopy(graph)
    graph.pop(META_KEY, None)
    return graph


def inject_seed(graph: dict, seed: int) -> dict:
    hit = False
    for node_id, node in graph.items():
        if node_id == META_KEY or not isinstance(node, dict):
            continue
        field = SEED_INPUTS.get(node.get("class_type", ""))
        if field:
            node.setdefault("inputs", {})[field] = seed
            hit = True
    if not hit:
        raise RenderError("시드 입력 노드가 없습니다 (KSampler/RandomNoise류 부재)")
    return graph
