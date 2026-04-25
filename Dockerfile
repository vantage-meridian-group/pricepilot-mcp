FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pricepilot_mcp/ pricepilot_mcp/

EXPOSE 8081

CMD ["python", "-m", "pricepilot_mcp", "--http", "--port", "8081"]
