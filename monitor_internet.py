"""
Monitor de Internet (estilo PingPlotter)
- Pinga vários alvos de 1 em 1 segundo
- Mostra gráfico de ping, perda de pacote e jitter
- Registra quedas, lags e perdas em eventos.log e todos os pings em pings.csv
Só usa bibliotecas que já vêm com o Python (sem pip install).
"""
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading, subprocess, re, time, sys, os, queue, collections, datetime, socket

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_EVENTOS = os.path.join(PASTA, "eventos.log")
ARQ_CSV = os.path.join(PASTA, "pings.csv")
INTERVALO = 1.0          # segundos entre pings
LIMITE_LAG = 150         # ms: acima disso conta como lag
JANELAS = {"Tudo": None, "1 min": 60, "5 min": 300, "15 min": 900, "1 hora": 3600}  # Tudo = desde que abriu
# "host:porta" = mede por conexão TCP (útil quando o servidor não responde ping, como jogos)
ALVOS_PADRAO = [("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Discord", "discord.com"),
                ("AWS São Paulo", "ec2.sa-east-1.amazonaws.com:443")]
JOGOS = {"client.exe": "Tibia", "tibia.exe": "Tibia", "valorant-win64-shipping.exe": "Valorant",
         "cs2.exe": "CS2", "csgo.exe": "CS:GO", "league of legends.exe": "LoL"}

BG, FG, CARD = "#1b1b1f", "#e6e6e6", "#25252b"
VERDE, AMARELO, VERMELHO = "#3ddc84", "#ffc247", "#ff4d4d"

WIN = sys.platform.startswith("win")
RE_TEMPO = re.compile(r"(?:time|tempo)\s*([=<])\s*([\d.,]+)\s*ms", re.I)
lock_arquivo = threading.Lock()
eventos = queue.Queue()


def agora_txt():
    return datetime.datetime.now().strftime("%d/%m %H:%M:%S")


def evento(msg, cor=FG):
    linha = f"{agora_txt()}  {msg}"
    with lock_arquivo:
        with open(ARQ_EVENTOS, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    eventos.put((linha, cor))


def rodar(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="ignore",
                              creationflags=(subprocess.CREATE_NO_WINDOW if WIN else 0)).stdout
    except Exception:
        return ""


def achar_gateway():
    """IP do seu roteador."""
    if WIN:
        for l in rodar(["route", "print", "-4", "0.0.0.0"]).splitlines():
            p = l.split()
            if len(p) >= 3 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0" and re.match(r"\d+\.\d+\.\d+\.\d+$", p[2]):
                return p[2]
    else:
        m = re.search(r"default via (\S+)", rodar(["ip", "route"]))
        return m.group(1) if m else None


def achar_provedor(gw):
    """Primeiro salto depois do roteador = equipamento do provedor."""
    cmd = (["tracert", "-d", "-h", "5", "-w", "1000", "1.1.1.1"] if WIN
           else ["traceroute", "-n", "-m", "5", "-w", "1", "1.1.1.1"])
    for l in rodar(cmd, 45).splitlines():
        m = re.match(r"^\s*(\d+)\s.*?(\d{1,3}(?:\.\d{1,3}){3})\s*$", l)
        if m and m.group(2) not in (gw, "1.1.1.1"):
            return m.group(2)


def detectar_jogos():
    """(Windows) acha os servidores TCP que os jogos abertos estão usando agora."""
    if not WIN:
        return []
    pids = {}
    for l in rodar(["tasklist", "/fo", "csv", "/nh"]).splitlines():
        p = [x.strip('"') for x in l.split('","')]
        if len(p) >= 2 and p[0].lower() in JOGOS:
            pids[p[1]] = JOGOS[p[0].lower()]
    achados = []
    for l in rodar(["netstat", "-ano", "-p", "tcp"]).splitlines():
        p = l.split()
        if len(p) >= 5 and p[3] == "ESTABLISHED" and p[4] in pids:
            ip, porta = p[2].rsplit(":", 1)
            if not ip.startswith(("127.", "192.168.", "10.")):
                achados.append((pids[p[4]], f"{ip}:{porta}"))
    return list(dict.fromkeys(achados))


def tcp_ping(host, porta):
    t = time.time()
    try:
        socket.create_connection((host, porta), timeout=1.5).close()
    except Exception:
        return None
    return (time.time() - t) * 1000


def ping_uma_vez(host):
    """Retorna latência em ms ou None se perdeu."""
    if ":" in host and host.rsplit(":", 1)[1].isdigit():
        h, p = host.rsplit(":", 1)
        return tcp_ping(h, int(p))
    if WIN:
        cmd = ["ping", "-n", "1", "-w", "1000", host]
        flags = subprocess.CREATE_NO_WINDOW
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
        flags = 0
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=4,
                           creationflags=flags, errors="ignore")
    except Exception:
        return None
    m = RE_TEMPO.search(r.stdout)
    if not m:
        return None
    if m.group(1) == "<":
        return 1.0
    return float(m.group(2).replace(",", "."))


class Alvo:
    def __init__(self, nome, host):
        self.nome, self.host = nome, host
        self.amostras = collections.deque()  # (timestamp, ms|None)
        self.rodando = True
        self.perdidos_seguidos = 0
        self.em_lag = False
        self.alerta_ate = 0.0   # enquanto > agora, o alvo pisca vermelho
        self.pico = 0.0
        self.inicio_lag = 0.0
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while self.rodando:
            t0 = time.time()
            rtt = ping_uma_vez(self.host)
            self.amostras.append((t0, rtt))
            with lock_arquivo:
                with open(ARQ_CSV, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.fromtimestamp(t0).isoformat(timespec='seconds')},"
                            f"{self.host},{'' if rtt is None else rtt}\n")
            self.analisar(t0, rtt)
            time.sleep(max(0, INTERVALO - (time.time() - t0)))

    def analisar(self, t, rtt):
        n = self.nome
        if rtt is None or rtt > LIMITE_LAG:
            self.alerta_ate = t + 4
        if rtt is None:
            if self.perdidos_seguidos == 0:
                evento(f"[{n}] perda de pacote", VERMELHO)
            self.perdidos_seguidos += 1
            return
        if self.perdidos_seguidos > 0:
            evento(f"[{n}] voltou após {self.perdidos_seguidos} pacote(s) perdido(s)", VERDE)
            self.perdidos_seguidos = 0
        if rtt > LIMITE_LAG:
            if not self.em_lag:
                self.em_lag, self.pico, self.inicio_lag = True, rtt, t
                evento(f"[{n}] LAG: ping subiu para {rtt:.0f} ms", AMARELO)
            self.pico = max(self.pico, rtt)
        elif self.em_lag:
            evento(f"[{n}] ping normalizou (pico {self.pico:.0f} ms, durou {t - self.inicio_lag:.0f}s)", VERDE)
            self.em_lag = False

    def janela(self, segundos):
        if segundos is None:
            return list(self.amostras)
        corte = time.time() - segundos
        return [(t, r) for t, r in list(self.amostras) if t >= corte]

    def stats(self, segundos):
        j = self.janela(segundos)
        if not j:
            return None
        ok = [r for _, r in j if r is not None]
        perda = 100 * (len(j) - len(ok)) / len(j)
        jit = (sum(abs(a - b) for a, b in zip(ok, ok[1:])) / (len(ok) - 1)) if len(ok) > 1 else 0
        atual = j[-1][1]
        return (atual, (sum(ok) / len(ok)) if ok else None,
                min(ok) if ok else None, max(ok) if ok else None, perda, jit)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Monitor de Internet")
        self.configure(bg=BG)
        self.geometry("1050x900")
        self.alvos, self.canvases, self.labels, self.piscas = [], {}, {}, {}
        self.em_queda = False
        self.inicio_queda = 0

        topo = tk.Frame(self, bg=BG)
        topo.pack(fill="x", padx=10, pady=8)
        tk.Label(topo, text="Janela:", bg=BG, fg=FG).pack(side="left")
        self.janela_var = tk.StringVar(value="Tudo")
        cb = ttk.Combobox(topo, textvariable=self.janela_var, values=list(JANELAS), width=7, state="readonly")
        cb.pack(side="left", padx=6)
        tk.Button(topo, text="🎮 Detectar jogos abertos", command=self.detectar).pack(side="left", padx=10)
        tk.Button(topo, text="+ Adicionar alvo", command=self.add_alvo).pack(side="left")
        self.status = tk.Label(topo, text="● OK", bg=BG, fg=VERDE, font=("Segoe UI", 12, "bold"))
        self.status.pack(side="right")

        self.lista = tk.Text(self, height=7, bg=CARD, fg=FG, bd=0, font=("Consolas", 10))
        self.lista.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        tk.Label(self, text="Eventos (também salvos em eventos.log)", bg=BG, fg=FG).pack(side="bottom", anchor="w", padx=10)
        self.painel = tk.Frame(self, bg=BG)
        self.painel.pack(fill="both", expand=True, padx=10)
        for nome, cor in (("v", VERDE), ("a", AMARELO), ("r", VERMELHO)):
            self.lista.tag_config(nome, foreground=cor)

        for nome, host in ALVOS_PADRAO:
            self.criar_alvo(nome, host)
        threading.Thread(target=self.descobrir_rede, daemon=True).start()
        self.after(1000, self.tick)
        self.after(400, self.piscar)

    def piscar(self):
        agora = time.time()
        fase = int(agora * 2.5) % 2 == 0
        for a, (cab, dot, lb, btn) in list(self.piscas.items()):
            if a.alerta_ate > agora:      # lagou / perdeu pacote agora: pisca vermelho
                bg, dot_cor = ("#6b1a1a" if fase else CARD), VERMELHO
            else:                         # estável: verde
                bg, dot_cor = CARD, VERDE
            dot.config(fg=dot_cor)
            for w in (cab, dot, lb, btn):
                w.config(bg=bg)
        self.after(400, self.piscar)

    def descobrir_rede(self):
        gw = achar_gateway()
        if gw:
            self.after(0, self.criar_alvo, "Roteador", gw)
        isp = achar_provedor(gw)
        if isp:
            self.after(0, self.criar_alvo, "Provedor (1º salto)", isp)

    def detectar(self):
        ja = {a.host for a in self.alvos}
        novos = [(n, h) for n, h in detectar_jogos() if h not in ja]
        for n, h in novos:
            self.criar_alvo(f"{n} servidor", h)
        if not novos:
            messagebox.showinfo("Detectar jogos",
                "Nenhum servidor novo encontrado.\nAbra o jogo (já logado/em partida) e clique de novo.\n"
                "Obs.: só acha conexões TCP (Tibia, LoL). Valorant/CS usam UDP: use os outros alvos como referência.")

    def remover(self, a, frame):
        a.rodando = False
        frame.destroy()
        self.alvos.remove(a)
        self.canvases.pop(a, None)
        self.labels.pop(a, None)
        self.piscas.pop(a, None)

    def add_alvo(self):
        host = simpledialog.askstring("Novo alvo",
            "IP ou site (ex: 192.168.0.1)\nOu IP:porta para medir por TCP (ex: 200.1.2.3:7171):", parent=self)
        if host:
            self.criar_alvo(host.strip(), host.strip())

    def criar_alvo(self, nome, host):
        a = Alvo(nome, host)
        self.alvos.append(a)
        f = tk.Frame(self.painel, bg=CARD)
        f.pack(fill="both", expand=True, pady=3)
        cab = tk.Frame(f, bg=CARD)
        cab.pack(fill="x", padx=6, pady=(4, 0))
        btn = tk.Button(cab, text="✕", bd=0, bg=CARD, fg="#999", command=lambda: self.remover(a, f))
        btn.pack(side="right")
        dot = tk.Label(cab, text="●", bg=CARD, fg=VERDE, font=("Segoe UI", 14))
        dot.pack(side="left")
        lb = tk.Label(cab, text=f"{nome} ({host})", bg=CARD, fg=FG, anchor="w", font=("Consolas", 10))
        lb.pack(side="left", fill="x")
        self.piscas[a] = (cab, dot, lb, btn)
        c = tk.Canvas(f, bg="#15151a", height=80, highlightthickness=0)
        c.pack(fill="both", expand=True, padx=6, pady=4)
        self.canvases[a], self.labels[a] = c, lb

    def tick(self):
        seg = JANELAS[self.janela_var.get()]
        for a in self.alvos:
            self.desenhar(a, seg)
        self.checar_queda()
        while not eventos.empty():
            linha, cor = eventos.get()
            tag = {VERDE: "v", AMARELO: "a", VERMELHO: "r"}.get(cor, "")
            self.lista.insert("1.0", linha + "\n", tag)
        n = max((len(a.amostras) for a in self.alvos), default=0)
        self.after(1000 if n < 50000 else 3000, self.tick)

    def checar_queda(self):
        """Queda = todos os alvos perdendo os últimos 3 pings."""
        ultimos = [list(a.amostras)[-3:] for a in self.alvos if len(a.amostras) >= 3]
        if not ultimos:
            return
        caiu = all(all(r is None for _, r in u) for u in ultimos)
        if caiu and not self.em_queda:
            self.em_queda, self.inicio_queda = True, time.time()
            self.status.config(text="● SEM INTERNET", fg=VERMELHO)
            evento("*** QUEDA DE INTERNET detectada ***", VERMELHO)
        elif not caiu and self.em_queda:
            self.em_queda = False
            self.status.config(text="● OK", fg=VERDE)
            evento(f"*** Internet voltou (ficou fora ~{time.time() - self.inicio_queda:.0f}s) ***", VERDE)

    def desenhar(self, a, seg):
        c = self.canvases[a]
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        c.delete("all")
        j = a.janela(seg)
        s = a.stats(seg)
        if s:
            atual, med, mn, mx, perda, jit = s
            f = lambda v: "--" if v is None else f"{v:.0f}"
            self.labels[a].config(
                text=f"{a.nome} ({a.host})   atual: {f(atual)} ms | média: {f(med)} | mín: {f(mn)} | "
                     f"máx: {f(mx)} | perda: {perda:.1f}% | jitter: {jit:.1f} ms",
                fg=FG)
        valores = [r for _, r in j if r is not None]
        valores = [r for _, r in j if r is not None]
        ymax = max(100, (max(valores) if valores else 0) * 1.15, LIMITE_LAG * 1.1)
        y = lambda v: h - 14 - (v / ymax) * (h - 20)
        agora = time.time()
        if seg is None:
            ini = j[0][0] if j else agora
            span = max(agora - ini, 60)
        else:
            ini, span = agora - seg, seg
        c.create_rectangle(0, 0, w, y(LIMITE_LAG), fill="#33191b", outline="")                 # zona vermelha
        c.create_rectangle(0, y(LIMITE_LAG), w, y(LIMITE_LAG * 0.5), fill="#33301a", outline="")  # amarela
        c.create_rectangle(0, y(LIMITE_LAG * 0.5), w, h - 14, fill="#14301f", outline="")        # verde
        c.create_line(0, y(LIMITE_LAG), w, y(LIMITE_LAG), fill="#886", dash=(4, 4))
        c.create_text(4, y(LIMITE_LAG) - 7, text=f"{LIMITE_LAG} ms", fill="#886", anchor="w", font=("Consolas", 8))
        c.create_text(w - 4, 8, text=f"{ymax:.0f} ms", fill="#666", anchor="e", font=("Consolas", 8))
        hora = lambda t: time.strftime("%H:%M:%S", time.localtime(t))
        c.create_text(4, h - 6, text=hora(ini), fill="#777", anchor="w", font=("Consolas", 8))
        c.create_text(w - 4, h - 6, text=hora(ini + span), fill="#777", anchor="e", font=("Consolas", 8))
        # agrupa as amostras por coluna de pixel (mín, máx, perdeu?) -> barrinhas estilo PingPlotter
        cols = {}
        for t, r in j:
            k = int((t - ini) / span * (w - 1))
            b = cols.get(k)
            if b is None:
                b = cols[k] = [None, None, False]
            if r is None:
                b[2] = True
            else:
                b[0] = r if b[0] is None or r < b[0] else b[0]
                b[1] = r if b[1] is None or r > b[1] else b[1]
        larg = max(2, min(6, round(w / span * INTERVALO)))
        for k, (mn, mx, perdeu) in cols.items():
            if mn is not None:
                cor = VERDE if mx < LIMITE_LAG * 0.5 else AMARELO if mx < LIMITE_LAG else VERMELHO
                y1, y2 = y(mx), y(mn)
                if y2 - y1 < 5:
                    y1 = y2 - 5
                c.create_line(k, y1, k, y2, fill=cor, width=larg)
            if perdeu:  # pacote perdido = tracinho vermelho de cima a baixo
                c.create_line(k, 0, k, h - 12, fill=VERMELHO, width=max(3, larg))


if __name__ == "__main__":
    App().mainloop()
