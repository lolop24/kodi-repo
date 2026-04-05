FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HELPER_HOST=0.0.0.0 \
    HELPER_PORT=8097 \
    HELPER_DATA_DIR=/data \
    HELPER_BROWSER=playwright \
    HELPER_USE_XVFB=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

EXPOSE 8097

CMD ["python", "app.py"]
