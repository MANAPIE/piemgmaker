# 프록시가 검증할 Bearer 토큰. TF는 컨테이너만 만들고 값은 넣지 않는다 —
# 값이 HCL에 들어가면 tfstate에 평문으로 남는다. 버전 주입은 사람이 gcloud로 한다:
#   printf '%s' "<토큰>" | gcloud secrets versions add pm-backend-token --data-file=- --project=<프로젝트>
resource "google_secret_manager_secret" "backend_token" {
  secret_id = "pm-backend-token"
  project   = var.project_id

  replication {
    auto {}
  }
}
