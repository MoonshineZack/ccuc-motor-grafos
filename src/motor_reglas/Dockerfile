FROM python:3.14.5-slim-trixie
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
CMD ["sleep", "infinity"]
