FROM python:3.9-alpine

WORKDIR /app
COPY json-log-processor.py .

# Устанавливаем только необходимые пакеты (исправленная строка)
RUN apk add --no-cache && \
    chmod +x json-log-processor.py

# Используем unbuffered output для немедленного вывода
ENV PYTHONUNBUFFERED=1

CMD ["python", "json-log-processor.py"]