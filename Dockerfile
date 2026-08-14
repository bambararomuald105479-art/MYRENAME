FROM python:3.11-slim

WORKDIR /app

# Copie les requirements
COPY requirements.txt .

# Installe les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code
COPY . .

CMD ["python", "main.py"]
