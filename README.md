# 📈 Chandelier Exit Alert Bot

Bot de alertas en tiempo real que replica el indicador **Chandelier Exit** de TradingView, utilizando velas tradicionales y Heikin Ashi.

Envia alertas a un canal de Discord cuando se detecta un cambio de tendencia, con mensajes personalizados y emojis.

---

## 🚀 Características

- ✅ Multi-símbolo (BTCUSDT, ETHUSDT)
- ✅ Multi-timeframe (5m hasta 1d)
- ✅ Velas japonesas y Heikin Ashi
- ✅ Reconexion automática de WebSocket
- ✅ Alertas formateadas vía Webhook de Discord
- ✅ Ligero y simple, sin frameworks innecesarios

---

## 📦 Requisitos

- Python 3.10 o superior
- Clave de API de Binance (modo lectura)
- Webhook de Discord

---

## 🛠️ Instalación

1. Cloná este repositorio:

```bash
git clone https://github.com/TU_USUARIO/chandelier-bot.git
cd chandelier-bot

2.Instalá dependencias:

pip install -r requirements.txt

3.Configurá tus variables en un archivo .env:

BINANCE_KEY=tu_api_key
BINANCE_SECRET=tu_api_secret
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...


Autor

Desarrollado por [Hernan Roda].
Código libre para fines personales y educativos.
Compatible con TradingView y Binance.


