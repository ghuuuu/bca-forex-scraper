# The DS916+ is an Intel Braswell (x86_64) box, so the stock python image runs
# natively — no emulation, no ARM variant to chase.
FROM python:3.12-slim

# The scraper has no dependencies, so there is nothing to pip install. That is
# the whole reason this image is small and has almost no patch surface.

WORKDIR /app
COPY bca_forex/ /app/bca_forex/

# Data lives on a mounted volume, never in the image: the container has to be
# replaceable without losing the history.
VOLUME ["/data"]

# Only affects log lines from libraries; the scraper computes WIB itself from a
# fixed +7 offset, so its slots hold even if this is wrong or unset.
ENV TZ=Asia/Jakarta \
    PYTHONUNBUFFERED=1

# PYTHONUNBUFFERED above matters more than it looks: without it Python blocks
# stdout into 8 KB chunks and `docker logs` shows nothing for hours, which looks
# exactly like a hung scraper.

HEALTHCHECK --interval=5m --timeout=20s --start-period=1m \
  CMD python -m bca_forex --db /data/bca-forex.sqlite3 status || exit 1

ENTRYPOINT ["python", "-m", "bca_forex", "--db", "/data/bca-forex.sqlite3"]
CMD ["run", "--chart", "/web/index.html"]
