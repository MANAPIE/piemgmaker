"""빌드 시점 의존성 가드 — 이미지에 굽는 모델 코드의 import가 실제로 해결되는지 검사한다.

커스텀 노드(ComfyUI-RMBG)는 노드 파일별로 import 실패를 잡아 건너뛰므로, 미선언 의존성이
있어도 노드는 정상 등록되고 실제 import는 모델 로드 시점에야 일어난다 — 빌드·기동·스모크를
모두 통과하고 첫 생성에서 터진다.

AST로 최상위 import만 추출해 find_spec으로 해결 여부를 본다. 모델 코드를 exec하지 않으므로
GPU도 네트워크도 필요 없다. find_spec은 점 경로까지 확인하므로 패키지 부재뿐 아니라
timm.models.layers 같은 deprecated 셰임이 상위 버전에서 제거되는 경우도 잡힌다.

사용법: python check_baked_deps.py <검사할 .py 경로> [...]
"""

import ast
import importlib.util
import os
import sys


def top_level_imports(path: str) -> set[str]:
    """파일의 최상위 import 대상을 모듈 경로 문자열로 모은다 (상대 import는 제외)."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def unresolvable(modules: set[str]) -> list[str]:
    missing = []
    for module in sorted(modules):
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(module)
        except (ImportError, ValueError) as exc:
            # 상위 패키지 자체가 없으면 find_spec이 ImportError를 낸다 — 이것도 누락이다.
            missing.append(f"{module} ({exc.__class__.__name__})")
    return missing


def main(paths: list[str]) -> int:
    if not paths:
        print("검사할 파일 경로가 필요하다", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        # 굽는 모델 코드는 형제 파일을 절대 경로처럼 import한다
        # (birefnet.py의 `from BiRefNet_config import BiRefNetConfig`).
        # transformers의 remote-code 로더가 파일 디렉토리를 sys.path에 올려 해결하므로
        # 검사도 같은 조건을 만든다 — 안 하면 형제 모듈이 거짓 누락으로 잡힌다.
        sibling_dir = os.path.dirname(os.path.abspath(path))
        if sibling_dir not in sys.path:
            sys.path.insert(0, sibling_dir)
        modules = top_level_imports(path)
        missing = unresolvable(modules)
        if missing:
            failed = True
            print(f"[FAIL] {path}: 해결 불가 import {len(missing)}개", file=sys.stderr)
            for module in missing:
                print(f"         - {module}", file=sys.stderr)
        else:
            print(f"[OK] {path}: import {len(modules)}개 전부 해결")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
