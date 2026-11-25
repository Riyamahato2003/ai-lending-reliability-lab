FROM node:22-alpine AS frontend-build
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY lending_lab ./lending_lab
COPY migrations ./migrations
COPY --from=frontend-build /ui/dist ./frontend/dist
RUN pip install --no-cache-dir '.[gemini]' \
    && groupadd --system app \
    && useradd --system --gid app --no-create-home app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
CMD ["uvicorn", "lending_lab.api:app", "--host", "0.0.0.0", "--port", "8000"]
