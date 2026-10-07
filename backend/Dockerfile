# Контекст сборки — корень репозитория (нужен src/jira_client):
#   docker build -f backend/Dockerfile .   (в compose это уже задано)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/srv:/srv/src
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
# клиент Jira (пишет отдельный агент; в образ попадает как есть, без тестов)
COPY src/jira_client ./src/jira_client
# секреты в образ не копируются: всё через env при запуске
RUN useradd --system --uid 10001 appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
