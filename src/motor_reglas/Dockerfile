FROM python:3.14.5-slim-trixie
WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Código del motor (reglas, calidad y Lambda de egress) dentro de la imagen.
# En local, docker-compose monta ./src:/app para hot-reloading.
COPY src /app
ENV PYTHONPATH=/app

# Consumidor SQS FIFO / Orquestador ECS Fargate.
# docker-compose lo sobreescribe con "sleep infinity" para el laboratorio.
CMD ["python", "/app/motor_reglas/main.py"]
