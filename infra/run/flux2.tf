# FLUX.2 계열 전용 서비스. 스택 실측 ~50GB로 L4에 들어가지 않는다.
# CPU 20 / 메모리 80GiB는 RTX PRO 6000 Blackwell의 강제 최소 사양이라 더 줄일 수 없다.
resource "google_cloud_run_v2_service" "flux2" {
  name     = "pm-comfy-flux2"
  project  = var.project_id
  location = var.region

  # 인증은 컨테이너 안의 Bearer 프록시가 한다. IAM 인증을 쓰면 모든 호스트에 ADC가 필요해진다.
  ingress = "INGRESS_TRAFFIC_ALL"

  # 되돌리기 경로가 terraform destroy다. 데이터는 모델 버킷에만 있어 서비스는 보호 불필요.
  deletion_protection = false

  template {
    service_account = local.run_service_account
    timeout         = local.request_timeout

    # 잡 상태가 인스턴스 메모리에 있어 폴링이 다른 인스턴스로 가면 유실된다.
    session_affinity = true

    # 존 이중화를 끄면 단가가 내려가고 쿼터도 첫 배포 시 자동 부여된다.
    gpu_zonal_redundancy_disabled = true

    # GPU 서비스의 Cloud Storage 모델 적재에 필수 — 빼면 인스턴스 대역폭이 600 Mbps로 묶인다.
    vpc_access {
      egress = "ALL_TRAFFIC"

      network_interfaces {
        network    = local.vpc_network
        subnetwork = local.vpc_subnetwork
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    node_selector {
      accelerator = "nvidia-rtx-pro-6000"
    }

    volumes {
      name = local.models_volume_name

      gcs {
        bucket        = local.models_bucket
        read_only     = true
        mount_options = local.models_mount_options
      }
    }

    containers {
      image = local.flux2_image

      ports {
        container_port = local.container_port
      }

      resources {
        limits = {
          cpu              = "20"
          memory           = "80Gi"
          "nvidia.com/gpu" = "1"
        }

        # GPU 인스턴스는 CPU 상시 할당(instance-based billing)이 필수다.
        cpu_idle = false
      }

      volume_mounts {
        name       = local.models_volume_name
        mount_path = local.models_mount_path
      }

      # HTTP 프로브는 프록시의 인증을 통과하지 못해 401로 실패한다.
      startup_probe {
        tcp_socket {
          port = local.container_port
        }

        period_seconds    = local.startup_probe_period_seconds
        timeout_seconds   = 5
        failure_threshold = local.startup_probe_failure_threshold
      }

      env {
        name  = "PM_IDLE_EXIT_SECONDS"
        value = tostring(var.idle_exit_seconds)
      }

      env {
        name = "PM_PROXY_TOKEN"

        value_source {
          secret_key_ref {
            secret  = local.backend_token_secret
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    # 이미지 롤아웃은 gcp/deploy.sh가 소유한다. TF가 같이 소유하면 배포마다 되돌리는 diff가 생긴다.
    # client·client_version은 gcloud 배포가 자기 값으로 갱신하는 필드다.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

# 인증은 컨테이너 프록시가 수행하므로 IAM 레벨은 열어 둔다.
resource "google_cloud_run_v2_service_iam_member" "flux2_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.flux2.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
