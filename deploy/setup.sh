#!/usr/bin/env bash
# Oracle Cloud Ubuntu 22.04 kurulum scripti
# Çalıştır: bash deploy/setup.sh

set -e

echo "==> Python ve bağımlılıklar kuruluyor..."
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

echo "==> Sanal ortam oluşturuluyor..."
python3.11 -m venv /home/ubuntu/.venv
source /home/ubuntu/.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Playwright Chromium kuruluyor..."
playwright install chromium --with-deps

echo "==> Log dizini oluşturuluyor..."
mkdir -p logs data/exports

echo "==> systemd servisi kuruluyor..."
sudo cp deploy/price-scraper.service /etc/systemd/system/
sudo cp deploy/price-scraper.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable  price-scraper.timer
sudo systemctl start   price-scraper.timer

echo ""
echo "==> Kurulum tamamlandı."
echo "    Timer durumu: sudo systemctl status price-scraper.timer"
echo "    Manuel test:  source /home/ubuntu/.venv/bin/activate && python -m pipeline.runner --dry-run"
