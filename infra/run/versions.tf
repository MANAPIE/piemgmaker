terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # backend 블록에는 변수를 쓸 수 없어 버킷·prefix를 비워 두고 Makefile이 init 시 -backend-config로 주입한다.
  # base root와 prefix가 겹치면 서로의 state를 덮어쓴다.
  backend "gcs" {}
}
