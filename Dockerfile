FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY dfsha ./dfsha
COPY tests ./tests
COPY scripts ./scripts
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
CMD ["uvicorn", "dfsha.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
