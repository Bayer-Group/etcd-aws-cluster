FROM gliderlabs/alpine:3.4

RUN apk --update add \
      python \
      py-pip \
      jq \
      curl \
      wget \
      bash &&\
      pip install --upgrade awscli &&\
      mkdir /root/.aws

COPY etcd-aws-cluster /etcd-aws-cluster

# Expose volume for adding credentials
VOLUME ["/root/.aws"]

# Expose directory to write output to, and to potentially read certs from
VOLUME ["/etc/sysconfig/", "/etc/certs"]

ENTRYPOINT /etcd-aws-cluster
