# latest Airflow image
FROM apache/airflow:3.1.7

COPY data_pipeline/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=airflow:0 savviocore /opt/airflow/savviocore
# TODO: Remove '-e' flag for production deployments so savviocore is baked into site-packages
RUN pip install --no-cache-dir -e /opt/airflow/savviocore

COPY --chown=airflow:0 data_pipeline/dags /opt/airflow/dags
