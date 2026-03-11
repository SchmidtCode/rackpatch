FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        docker.io \
        docker-compose \
        git \
        jq \
        openssh-client \
        rsync \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    ansible-core==2.18.3 \
    croniter \
    docker \
    jmespath \
    netaddr \
    proxmoxer \
    requests

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml

WORKDIR /workspace
COPY api /opt/ops-api
COPY scripts/prepare_ssh_dir.sh /usr/local/bin/prepare_ssh_dir.sh

CMD ["/opt/ops-api/start-controller.sh"]
