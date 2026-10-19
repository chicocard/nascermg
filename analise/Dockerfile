FROM python:3.12-slim
# gcc: o PyTensor (PyMC) usa o compilador C quando disponível; o nutpie amostra pelo backend numba
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /analise
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ENV PYTHONUNBUFFERED=1 PYTENSOR_FLAGS="floatX=float64" OMP_NUM_THREADS=2 NUMBA_CACHE_DIR=/tmp/numba
CMD ["sh", "-c", "python cluster_municipios.py && python correlacoes.py && python espacial.py"]
