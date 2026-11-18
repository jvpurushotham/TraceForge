# One Dockerfile for all FastAPI services.  Build context = repository root.
#   docker build -f services/Dockerfile --build-arg SERVICE=order-service --build-arg PORT=8000 -t traceforge/order-service .
FROM python:3.12-slim
ARG SERVICE
ARG PORT=8000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=${PORT}
WORKDIR /srv
COPY services/requirements.txt /tmp/requirements.txt
COPY services/${SERVICE}/requirements.txt /tmp/service-requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/service-requirements.txt
COPY services/common /srv/common
COPY services/${SERVICE}/app /srv/app
RUN useradd -r -u 10001 app && chown -R app /srv
USER app
EXPOSE ${PORT}
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
