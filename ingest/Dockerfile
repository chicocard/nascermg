FROM python:3.12-slim

# gcc é necessário para compilar o pyreaddbc (descompactador de .dbc do DATASUS)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY download_datasus.py territorio.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "download_datasus.py"]
