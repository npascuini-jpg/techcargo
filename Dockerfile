# TechCargo — Web Server (Python)
FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY server.py techcargo_sync.py ./

# Directorio de datos persistentes (montar como volumen)
RUN mkdir -p /data
ENV APP_DATA_DIR=/data

# Puerto
EXPOSE 8080

CMD ["python", "server.py"]
