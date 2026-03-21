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
        rsync \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    ansible-core==2.18.3 \
    croniter \
    docker \
    jmespath \
    netaddr \
    requests

COPY requirements-rackpatch.txt /tmp/requirements-rackpatch.txt
COPY requirements.yml /tmp/requirements.yml
RUN pip install --no-cache-dir -r /tmp/requirements-rackpatch.txt \
    ansible-core==2.18.3 \
    && ansible-galaxy collection install -r /tmp/requirements.yml

WORKDIR /opt/rackpatch
COPY ansible.cfg /opt/rackpatch/ansible.cfg
COPY app /opt/rackpatch/app
COPY playbooks /opt/rackpatch/playbooks
COPY roles /opt/rackpatch/roles
COPY scripts /opt/rackpatch/scripts
COPY sites/example /opt/rackpatch/sites/example

ENV PYTHONPATH=/opt/rackpatch/app \
    PYTHONUNBUFFERED=1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "9080"]
