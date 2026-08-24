FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY criteria.yaml .

# resume/ and data/ are meant to be bind-mounted in docker-compose.yml so the
# user's resume and the SQLite DB persist outside the container.
RUN mkdir -p resume data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
