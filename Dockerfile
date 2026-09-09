FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app
COPY pyproject.toml README.md ./
COPY career_radar ./career_radar
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "career_radar.web:app", "--host", "0.0.0.0", "--port", "8000"]
