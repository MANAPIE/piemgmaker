locals {
  # 50%에서 인지, 90%에서 사용 중단 판단, 100%에서 초과 확정.
  budget_threshold_percents = [0.5, 0.9, 1.0]
}

# budget_filter.projects는 프로젝트 ID가 아니라 프로젝트 번호를 요구한다.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_monitoring_notification_channel" "budget_email" {
  project      = var.project_id
  display_name = "PIEmgmaker 예산 알림"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }
}

# GPU 시간 과금은 실수 한 번에 하루치 예산을 태울 수 있어 알림이 유일한 자동 안전망이다.
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "piemgmaker-monthly"

  budget_filter {
    projects               = ["projects/${data.google_project.this.number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      # currency_code는 일부러 비운다 — 지정하면 결제 계정 통화와 일치해야 하고
      # (불일치 시 400), 비우면 결제 계정 통화를 그대로 쓴다. units도 그 통화 단위다.
      units = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = local.budget_threshold_percents

    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.id]
    schema_version                   = "1.0"
    # 결제 계정이 회사 프로젝트들과 공유라, 기본값(false)이면 결제 계정 관리자
    # 전원에게 이 프로젝트의 예산 메일이 간다. alert_email에게만 보낸다.
    disable_default_iam_recipients = true
  }
}
