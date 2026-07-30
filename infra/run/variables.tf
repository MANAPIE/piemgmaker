variable "project_id" {
  description = "GCP 프로젝트 ID. base root와 같은 프로젝트여야 한다."
  type        = string
}

variable "region" {
  description = "Cloud Run 리전. L4와 RTX PRO 6000이 모두 제공되는 리전이어야 한다."
  type        = string
  default     = "asia-southeast1"
}

# 이미지 롤아웃은 gcp/deploy.sh가 소유하고 TF는 ignore_changes로 무시한다.
# 여기 값은 서비스를 처음 만들 때만 쓰인다 — 비우면 deploy.sh --initial이 올린
# bootstrap 태그를 쓴다 (locals.tf에서 파생).
variable "qwen_image" {
  description = "pm-comfy-qwen 초기 컨테이너 이미지. 비우면 AR의 bootstrap 태그."
  type        = string
  default     = ""
}

variable "flux2_image" {
  description = "pm-comfy-flux2 초기 컨테이너 이미지. 비우면 AR의 bootstrap 태그."
  type        = string
  default     = ""
}

variable "idle_exit_seconds" {
  description = "자기 종료 와치독의 유휴 임계(초). 0이면 비활성 — 유휴 꼬리 과금이 그대로 붙는다."
  type        = number

  # 유휴와 모델 적재가 같은 인스턴스 요율로 과금돼 손익 분기점이 "적재 소요 시간"과 같아진다
  # — 조절 가능한 전 구간이 그보다 아래이므로 살려두는 쪽이 항상 이득이다. Cloud Run이 GPU
  # 인스턴스를 유휴 10분에 회수하므로 9분이 실질 상한이다.
  default = 540

  validation {
    condition     = var.idle_exit_seconds >= 0
    error_message = "idle_exit_seconds는 음수일 수 없습니다."
  }
}
