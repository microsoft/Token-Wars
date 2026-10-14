FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/app/data
WORKDIR /app
COPY server.py .
RUN useradd --create-home --uid 10001 tokenwars && mkdir -p /app/data && chown -R tokenwars /app
USER tokenwars
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'))" || exit 1
CMD ["python", "server.py"]
