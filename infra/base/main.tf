# 컨테이너 이미지 저장소. 이미지 빌드·push는 TF가 아니라 gcp/deploy.sh가 소유한다.
resource "google_artifact_registry_repository" "comfy" {
  repository_id = "pm-comfy"
  project       = var.project_id
  location      = var.region
  format        = "DOCKER"
  description   = "PIEmgmaker 원격 ComfyUI 컨테이너 이미지"
}

# Cloud Run 런타임 신원. 기본 Compute SA(광범위 권한)를 쓰지 않기 위해 전용으로 만든다.
resource "google_service_account" "run" {
  account_id   = "pm-run-sa"
  project      = var.project_id
  display_name = "PIEmgmaker Cloud Run 런타임"
}

# 모델은 읽기만 한다 — 컨테이너가 원본을 덮어쓰거나 지울 수 없게 objectViewer만 준다.
resource "google_storage_bucket_iam_member" "run_models_reader" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.run.member
}

resource "google_secret_manager_secret_iam_member" "run_backend_token_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.backend_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.run.member
}
