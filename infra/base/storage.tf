# 모델 버킷 — Cloud Run이 GCS FUSE로 읽기 전용 마운트한다.
# 멀티리전이면 Cloud Run과 다른 리전에서 읽어 전송비·지연이 붙으므로 단일 리전으로 고정한다.
resource "google_storage_bucket" "models" {
  name     = "${var.project_id}-models"
  project  = var.project_id
  location = var.region

  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # 모델 파일은 로컬에서 언제든 재업로드 가능하다. 100GB 규모라 버전 보관은 순수 비용이다.
  versioning {
    enabled = false
  }

  # 기본값 7일 soft delete는 교체·삭제한 모델을 보관 기간 동안 이중 과금한다.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  # 재업로드에 수 시간이 드는 자산이라 실수로 지워지지 않게 이중으로 막는다.
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

# 서버사이드 모델 적재(gcp/fetch_models_remote.sh)용 — 기본 풀 Cloud Build는
# Compute 기본 SA로 실행되며, 이 권한이 없으면 업로드가 403으로 실패한다.
resource "google_storage_bucket_iam_member" "models_build_writer" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# objectAdmin에는 buckets.get이 없어 gcloud가 병렬 컴포지트 업로드 가능 여부를 확인하지 못하고
# 단일 스트림으로 폴백한다(30GB급 업로드가 수 배 느려짐). 버킷 조회만 추가로 연다.
resource "google_storage_bucket_iam_member" "models_build_bucket_reader" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# 빌드 로그를 Cloud Logging에 남기기 위한 권한 — 없으면 콘솔 로그가 비어 경고가 뜬다.
resource "google_project_iam_member" "build_logs_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# gcloud builds submit(소스 업로드 방식, gcp/deploy.sh)의 소스 버킷 읽기 권한.
# <project>_cloudbuild 버킷은 첫 submit 때 gcloud가 자동 생성한다(TF 소유 아님) —
# 새 프로젝트라면 deploy를 한 번 시도해 버킷이 생긴 뒤 이 리소스를 apply해야 한다.
resource "google_storage_bucket_iam_member" "cloudbuild_source_reader" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# 빌드 결과 이미지를 AR에 push할 권한 — 새 프로젝트의 기본 빌드 SA(Compute 기본 SA)는
# 프로젝트 롤이 없어 소스 읽기·로그·push 전부를 명시적으로 부여해야 한다.
resource "google_artifact_registry_repository_iam_member" "comfy_build_writer" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.comfy.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}
