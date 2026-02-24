FROM public.ecr.aws/lambda/python:3.11

COPY universe.py lambda_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.lambda_handler"]
