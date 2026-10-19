FROM python:3.12-slim
WORKDIR /dbt
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ENV PYTHONUNBUFFERED=1 DBT_PROFILES_DIR=/dbt
CMD ["dbt", "build"]
