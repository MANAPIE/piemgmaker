output "models_bucket" {
  description = "모델 버킷 이름. gcp/sync_models.sh와 run root의 FUSE 볼륨이 참조한다."
  value       = google_storage_bucket.models.name
}

output "artifact_registry_repository" {
  description = "컨테이너 이미지 리포 경로. 이미지 태그는 <이 값>/<이미지명>:<태그> 형태가 된다."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.comfy.repository_id}"
}

output "run_service_account_email" {
  description = "Cloud Run 런타임 서비스 계정. run root가 이름 규약으로 참조한다."
  value       = google_service_account.run.email
}

output "vpc_network" {
  description = "Direct VPC egress용 네트워크 이름. run root가 이름 규약으로 참조한다."
  value       = google_compute_network.main.name
}

output "vpc_subnetwork" {
  description = "Direct VPC egress용 서브넷 이름 (Private Google Access 활성). run root가 참조한다."
  value       = google_compute_subnetwork.run.name
}

output "backend_token_secret_id" {
  description = "프록시 토큰 시크릿 컨테이너 ID. 값은 별도로 주입해야 한다."
  value       = google_secret_manager_secret.backend_token.secret_id
}
