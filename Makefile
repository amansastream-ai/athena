.PHONY: help install dev test migrate revision seed verify backup restore docker smoke clean

help:
	@echo "Jaay — commandes : install | start | dev | test | migrate | revision | seed | verify | backup | restore | docker | smoke"

install:
	python3 -m pip install -r requirements.txt
	cp -n .env.example .env || true
	python3 -m scripts.seed --demo

dev:
	JAAY_ENV=dev PORT=8000 RELOAD=1 bash scripts/start.sh

start:
	JAAY_ENV=dev PORT=8000 bash scripts/start.sh

test:
	python3 -m pytest -q

migrate:
	python3 -m alembic upgrade head

revision:
	python3 -m alembic revision --autogenerate -m "$(m)"

seed:
	python3 -m scripts.seed --demo

verify:
	python3 -m scripts.verify_integrity

backup:
	./scripts/backup.sh

restore:
	@echo "Usage : make restore FILE=backups/jaay-YYYYMMDD-HHMMSS.sql.gz"
	./scripts/restore.sh $(FILE)

docker:
	cp -n .env.example .env || true
	docker compose up --build -d
	@echo "Application : http://localhost:8000"

smoke:
	./scripts/smoke.sh

clean:
	rm -rf var/*.db var/*.db-wal var/*.db-shm .pytest_cache
