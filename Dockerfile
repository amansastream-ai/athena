FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/jaay
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p var media && touch media/.gitkeep && chmod +x ops/entrypoint.sh scripts/*.sh

# JAAY_SECRET_KEY et JAAY_DATABASE_URL sont fournis au RUNTIME uniquement
# (.env local pour docker compose, variables d'environnement pour Vercel).
# Aucun mot de passe ne doit etre ecrit dans l'image.
ENV JAAY_ENV=production \
    JAAY_MEDIA_DIR=/srv/jaay/media \
    PORT=8000

EXPOSE 8000
ENTRYPOINT ["ops/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
