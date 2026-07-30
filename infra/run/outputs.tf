output "qwen_url" {
  description = "PM_REMOTE_URL_QWEN에 넣을 서비스 URL."
  value       = google_cloud_run_v2_service.qwen.uri
}

output "flux2_url" {
  description = "PM_REMOTE_URL_FLUX2에 넣을 서비스 URL."
  value       = google_cloud_run_v2_service.flux2.uri
}

output "service_names" {
  description = "gcp/deploy.sh가 이미지를 갱신할 대상 서비스 이름."
  value = {
    qwen  = google_cloud_run_v2_service.qwen.name
    flux2 = google_cloud_run_v2_service.flux2.name
  }
}
