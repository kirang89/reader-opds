FROM python:3.12.12-slim

WORKDIR /app
COPY reader_opds ./reader_opds
RUN useradd --system --no-create-home app
USER app
EXPOSE 8080
CMD ["python", "-m", "reader_opds", "--host", "0.0.0.0", "--port", "8080"]
