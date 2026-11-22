terraform {
  backend "gcs" {
    bucket = "trading-bot-515509-tfstate"
    prefix = "bootstrap"
  }
}
