FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default DB path inside container — mount a volume here for persistence
ENV NOVA_DB_PATH=/data/nova.db
VOLUME ["/data"]

EXPOSE 8766

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8766"]
