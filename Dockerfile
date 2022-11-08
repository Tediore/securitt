FROM python:3

ADD securitt.py /

RUN pip install paho.mqtt requests pyyaml

CMD [ "python", "./securitt.py" ]
