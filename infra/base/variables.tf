variable "project_id" {
  description = "GCP 프로젝트 ID. bootstrap.sh로 만든 전용 프로젝트."
  type        = string
}

variable "region" {
  description = "리소스 리전. Cloud Run GPU에 서울이 없어 최인접 싱가포르를 쓴다."
  type        = string
  default     = "asia-southeast1"
}

variable "billing_account" {
  description = "예산 알림을 걸 결제 계정 ID. gcloud billing accounts list로 확인한다."
  type        = string
}

variable "budget_amount" {
  description = "월 예산 한도 — 결제 계정 통화 단위 (KRW 계정이면 원, USD 계정이면 달러). GPU 과금 폭주 안전망."
  type        = number

  validation {
    condition     = var.budget_amount > 0
    error_message = "budget_amount는 0보다 커야 합니다."
  }
}

variable "alert_email" {
  description = "예산 임계 초과 알림을 받을 이메일 주소."
  type        = string
}
