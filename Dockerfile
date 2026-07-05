FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install \
      "git+https://github.com/gellsmore-svg/keturah.git" \
      "git+https://github.com/gellsmore-svg/galeed.git" \
      "git+https://github.com/gellsmore-svg/cairn.git" \
    && python -m pip install ".[profiles,web]"

EXPOSE 8765

CMD ["tirzah", "serve", "--host", "0.0.0.0", "--port", "8765"]
