# One image for all PressWatch services (api, cell, collector); the command differs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home --uid 1000 presswatch

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

USER presswatch
EXPOSE 8000 4840
CMD ["presswatch", "api", "--host", "0.0.0.0", "--port", "8000"]
