FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir boto3

COPY universe.py lambda_handler.py ecs_entrypoint.py ./

CMD ["python", "-u", "ecs_entrypoint.py"]
