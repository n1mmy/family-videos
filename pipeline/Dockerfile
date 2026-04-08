FROM python:3.12-alpine

RUN apk add --no-cache ffmpeg

RUN pip install --no-cache-dir jsonschema

COPY pipeline/parse.py pipeline/transcode.py /app/
COPY manifest.schema.json /app/

WORKDIR /app

ENTRYPOINT ["python3", "transcode.py"]
