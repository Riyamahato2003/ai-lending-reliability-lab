FROM node:22-alpine AS frontend-build
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY lending_lab ./lending_lab
COPY migrations ./migrations
COPY --from=frontend-build /ui/dist ./frontend/dist
RUN pip install --no-cache-dir '.[gemini]'
EXPOSE 8000
CMD ["uvicorn", "lending_lab.api:app", "--host", "0.0.0.0", "--port", "8000"]
