output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "app_name" {
  value = var.app_name
}

output "alert_email" {
  value = var.alert_email
}

output "state_bucket" {
  value = google_storage_bucket.state.name
}

output "artifact_registry_repo" {
  description = "Image path prefix: <region>-docker.pkg.dev/<project>/<repo>"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "sa_deployer" {
  value = google_service_account.ci["ci-deployer"].email
}

output "sa_infra_plan" {
  value = google_service_account.ci["ci-infra-plan"].email
}

output "sa_infra_apply" {
  value = google_service_account.ci["ci-infra-apply"].email
}

output "sa_prod_runtime" {
  description = "The prod job's runtime identity (created here so it can read the owner-only operator secret)."
  value       = google_service_account.prod_runtime.email
}

output "operator_secret" {
  value = google_secret_manager_secret.operator.secret_id
}
