FROM python:3.12-slim

# No external Python deps — pure stdlib (http.server, threading, json)
WORKDIR /app/dashboard

# Dashboard files come from a bind mount at runtime, but copy them
# in here too so the image is self-contained for prod use.
COPY . /app/dashboard/

EXPOSE 8080

CMD ["python", "-u", "serve.py"]
