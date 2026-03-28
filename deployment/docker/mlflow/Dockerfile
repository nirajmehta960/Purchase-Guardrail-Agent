FROM ghcr.io/mlflow/mlflow:v2.17.2

RUN pip install psycopg2-binary google-cloud-storage

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
