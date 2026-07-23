FROM python:3.11-slim
WORKDIR /app
COPY app.py .
ENV PORT=8077
EXPOSE 8077
CMD ["python3", "app.py"]
