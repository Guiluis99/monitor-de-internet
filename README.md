# Monitor de Internet

Aplicativo desktop em Python para monitorar a conexão em tempo real, no estilo do PingPlotter. Mostra ping, perda de pacote, jitter e quedas, com gráficos e registro de eventos.

## Funcionalidades
- Ping de 1 em 1 segundo para vários alvos (Cloudflare, Google, Discord, AWS São Paulo)
- Detecção automática do roteador e do primeiro equipamento do provedor
- Gráfico de barras com faixas verde, amarela e vermelha; perda de pacote aparece como traço vermelho
- Indicador que pisca em vermelho quando o alvo lagar ou perder pacote
- Janela de tempo "Tudo" (desde que o app abriu) ou 1 min, 5 min, 15 min e 1 hora
- Botão para detectar servidores de jogos abertos (conexões TCP, como Tibia e LoL)
- Alvos personalizados por IP ou IP:porta (medição por TCP)
- Registro em `eventos.log` (quedas, lags e perdas) e `pings.csv` (todos os pings)

## Como usar
1. Instale o Python 3 (python.org) e marque "Add Python to PATH".
2. Baixe o `monitor_internet.py`.
3. Rode: `python monitor_internet.py`

Usa apenas bibliotecas que já vêm com o Python (tkinter). Feito para Windows.

## Observação
Projeto desenvolvido com auxílio de IA.
