FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY kaisen ./kaisen
RUN mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn kaisen.main:app_factory --factory --host 0.0.0.0 --port 8080"]
