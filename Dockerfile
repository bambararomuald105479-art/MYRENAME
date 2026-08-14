FROM python:3.11-slim

# Installe NTP client (ntpdate remplacé par ntpsec-ntpdate dans Debian Trixie)
RUN apt-get update && apt-get install -y ntpsec-ntpdate && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copie les requirements
COPY requirements.txt .

# Installe les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code
COPY . .

# Synchronise l'horloge au démarrage PUIS lance le bot
CMD ntpdate pool.ntp.org 2>/dev/null || true && sleep 2 && python main.py
