# Direct VPC egress용 네트워크. Cloud Run GPU가 Cloud Storage에서 모델을 적재할 때
# Google이 Direct VPC + Private Google Access를 요구사항으로 명시한다 — 없으면 인스턴스
# 대역폭이 600 Mbps로 묶인다. PGA 덕분에 외부 IP·NAT 없이 Google API에 닿으므로
# 그 둘은 만들지 않는다 (이 서비스는 Google API 외의 인터넷 목적지가 없다).
resource "google_compute_network" "main" {
  project = var.project_id
  name    = "pm-comfy"

  # 리전마다 자동 서브넷을 만들지 않는다 — 쓰는 리전 하나만 명시적으로 둔다.
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "run" {
  project = var.project_id
  name    = "pm-comfy-run"
  region  = var.region
  network = google_compute_network.main.id

  # Direct VPC는 인스턴스마다 이 범위에서 IP를 잡는다. 두 서비스가 max-instances=1이라
  # 실수요는 극소이고, /24는 향후 서비스 추가까지 여유가 있다.
  ip_cidr_range = "10.8.0.0/24"

  # 외부 IP 없이 Google API에 내부 경로로 접근한다 — 위 주석의 PGA 요구사항.
  private_ip_google_access = true
}
