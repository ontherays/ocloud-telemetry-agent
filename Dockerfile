FROM python:3.11-slim

# perf must match the running kernel major.minor. joule runs 6.6.0-1-rt-amd64.
# linux-perf from Debian trixie is close enough for stat -e on generic events;
# if it warns about version mismatch, build perf from the matching kernel tree.
RUN apt-get update \
 && apt-get install -y --no-install-recommends linux-perf procps \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask

WORKDIR /opt/agent
COPY agent/ ./agent/
COPY tools/ ./tools/

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "agent.main"]
CMD ["serve"]
