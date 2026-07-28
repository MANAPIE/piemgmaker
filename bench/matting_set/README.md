# 배경 제거 비교 테스트 세트

- **불투명**: 쇼핑백 · 화분류
- **반투명**: 유리컵 · 반투명 리본 · 젤리 질감

브리프는 `briefs/`, 입력 이미지는 `images/`, 세트 정의는 `set.yaml`에 포함되어 있다. 기준 알파(`gt_alpha`)가 있는 항목만 MAE·boundary MAE가 계산되고, 없으면 색 오염·속도만 측정된다.

## 세트 형식 (`set.yaml`)

```yaml
items:
  - id: shopping-bag-01
    rgb: images/shopping-bag-01.png
    gt_alpha: gt/shopping-bag-01.png   # 선택
    material: opaque
  - id: glass-cup-01
    rgb: images/glass-cup-01.png
    material: translucent
```

## 실행

```bash
uv run piemgmaker bench --set bench/matting_set/set.yaml --dry-run          # 세트 검증
uv run piemgmaker bench --set bench/matting_set/set.yaml --out out/matting-comparison.md
```

전략 러너는 `src/piemgmaker/matting_runners.py`에 구현되어 있으며 `cli bench`가 이를 사용한다. 실 모델 실행은 해당 ComfyUI 커스텀 노드 설치가 전제다.
