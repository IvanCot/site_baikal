FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app
COPY --chown=app:app . .
RUN mkdir -p media staticfiles && chown -R app:app media staticfiles && chmod 700 media
USER app
EXPOSE 8000
CMD ["sh", "docker/entrypoint.sh"]
