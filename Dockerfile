FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Europe/Madrid
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY config.yaml .
CMD ["python", "-m", "bot"]
