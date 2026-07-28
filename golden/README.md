# 골든 세트

회귀 검증용 기준 세트. 케이스(브리프 + 시드 + 버전 핀)와 기준 산출물(RGBA)을 비교해 생성 파이프라인의 회귀를 잡는다. 케이스는 `cases/`, 기준 산출물은 `expected/`에 둔다.

## 케이스 형식 (`cases/*.yaml`)

```yaml
id: shopping-bag-glossy        # 산출물 파일명 규약: <produced-dir>/<id>.png
expected: ../expected/shopping-bag-glossy.png
thresholds:                    # 임계값은 config가 아니라 케이스 파일에 명시
  ssim_min: 0.97
  iou_min: 0.98
  mae_max: 0.01
```

## 실행

```bash
uv run piemgmaker golden --cases golden/cases --produced <산출물 디렉토리>
```

지표: RGB SSIM(중립 회색 합성 후) + 알파 이진화 IoU + soft alpha MAE.
