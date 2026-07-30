provider "google" {
  project = var.project_id
  region  = var.region

  # 사용자 ADC는 quota project가 없어 billingbudgets 같은 일부 API 호출이
  # gcloud 공용 클라이언트 프로젝트로 귀속돼 403(SERVICE_DISABLED)이 난다.
  # provider가 이 프로젝트를 quota project로 명시해 ADC 상태와 무관하게 동작시킨다.
  # (귀속 대상이 되는 API들은 bootstrap.sh의 REQUIRED_APIS에 활성화돼 있어야 한다)
  user_project_override = true
  billing_project       = var.project_id
}
