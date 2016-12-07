FROM python:3.5-alpine

RUN pip install --upgrade boto3 requests &&\
    mkdir /root/.aws

COPY cluster.py /cluster.py

# Expose volume for adding credentials
VOLUME ["/root/.aws"]

# Expose directory to write output to, and to potentially read certs from
VOLUME ["/etc/sysconfig/", "/etc/certs"]

CMD python /cluster.py
