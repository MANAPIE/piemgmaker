locals {
  # base root가 만든 리소스를 이름 규약으로 참조한다.
  # terraform_remote_state로 묶으면 run root가 base state에 읽기 결합돼 root 분리 의미가 사라진다.
  models_bucket        = "${var.project_id}-models"
  run_service_account  = "pm-run-sa@${var.project_id}.iam.gserviceaccount.com"
  backend_token_secret = "pm-backend-token"

  vpc_network    = "pm-comfy"
  vpc_subnetwork = "pm-comfy-run"

  models_volume_name = "models"
  models_mount_path  = "/models"

  # gcsfuse 마운트 옵션 — 두 서비스가 같은 버킷을 같은 방식으로 읽으므로 공유한다.
  #   implicit-dirs         : Cloud Run 기본값이라 no-op이지만, 끄면 프리픽스 목록·읽기가
  #                           깨지므로(플레이스홀더 객체 없음) 의도를 명시해 둔다.
  #   enable-buffered-read  : 대용량 순차 읽기를 비동기 프리페치로 바꾼다. 파일 캐시
  #                           (cache-dir)와 상호 배타이며 cache-dir이 우선한다.
  #   read-global-max-blocks: 위 버퍼의 블록 수 상한. 이 메모리는 컨테이너 한도에 계산되므로
  #                           무제한(-1)으로 두지 않는다.
  #   metadata-cache-*      : 읽기 전용 마운트라 만료시킬 이유가 없다. Rapid Cache는
  #                           메타데이터를 캐시하지 않으므로 이 설정이 특히 필요하다.
  #
  # 파일 캐시(cache-dir)는 쓸 수 없다 — Cloud Run은 캐시 디렉토리로 in-memory 볼륨만 받고
  # 그 용량이 컨테이너 메모리에 계산된다. qwen은 32GiB에 모델이 24.65GB라 성립하지 않는다.
  models_mount_options = [
    "implicit-dirs",
    "enable-buffered-read=true",
    "read-global-max-blocks=64",
    "metadata-cache-ttl-secs=-1",
    "stat-cache-max-size-mb=64",
  ]

  # 이미지 변수 미지정 시 gcp/deploy.sh --initial이 올린 bootstrap 태그를 쓴다.
  # 생성 시 1회만 의미가 있고 이후 롤아웃은 ignore_changes로 deploy.sh 소관이다.
  bootstrap_image = "${var.region}-docker.pkg.dev/${var.project_id}/pm-comfy/pm-comfy:bootstrap"
  qwen_image      = var.qwen_image != "" ? var.qwen_image : local.bootstrap_image
  flux2_image     = var.flux2_image != "" ? var.flux2_image : local.bootstrap_image

  # 생성 1건이 분 단위라 기본 300초로는 모자란다.
  request_timeout = "3600s"

  # 프록시가 바인딩하는 포트. Cloud Run이 PORT 환경변수로도 같은 값을 넣어 준다.
  container_port = 8080

  # 프록시는 즉시 바인딩하고 모델 로드는 그 뒤에 일어난다. 이미지 pull이 느린 경우까지만 감안한다.
  startup_probe_period_seconds    = 10
  startup_probe_failure_threshold = 24
}
