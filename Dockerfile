FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRAME_PLAN_DATA=/data \
    PORT=8765

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ .

# the Git commit this image was built from, shown at the bottom of the web page
ARG REVISION=dev
ENV APP_VERSION=$REVISION

VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8765'), timeout=4)"
CMD ["python", "web.py"]
