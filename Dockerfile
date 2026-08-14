FROM python:3.11-slim

# Installe NTP client
RUN apt-get update && apt-get install -y ntpdate && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copie les requirements
COPY requirements.txt .

# Installe les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code
COPY . .

# Synchronise l'horloge au démarrage PUIS lance le bot
CMD ntpdate -u pool.ntp.org 2>/dev/null || true && sleep 2 && python main.py
