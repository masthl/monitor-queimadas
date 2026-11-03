"""
Monitor de Queimadas · DATAHORIZO
---------------------------------
Baixa os focos de calor do INPE (BDQueimadas, dados abertos) e, opcionalmente,
da NASA FIRMS; filtra pelo recorte do config.json; calcula alertas de focos
próximos às áreas monitoradas e grava tudo em docs/data/ para o WebGIS.

Uso:
    python scripts/atualizar_focos.py          # dados reais
    python scripts/atualizar_focos.py --demo   # dados sintéticos para testar

Variáveis de ambiente opcionais (GitHub > Settings > Secrets):
    FIRMS_MAP_KEY        chave gratuita da NASA FIRMS (complementa o dia corrente)
    TELEGRAM_BOT_TOKEN   token do bot que envia os alertas
    TELEGRAM_CHAT_ID     chat/grupo que recebe os alertas
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "docs" / "data"
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))

INPE_DIARIO = (
    "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/diario/"
    "Brasil/focos_diario_br_{data}.csv"
)
FIRMS_AREA = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{fonte}/{bbox}/{dias}"
)

CAMPOS = ["lat", "lon", "t", "sat", "mun", "uf", "dsc", "risco", "frp", "bioma", "fonte"]
SESSAO = requests.Session()
SESSAO.headers["User-Agent"] = "datahorizo-monitor-queimadas/1.0"


# ----------------------------------------------------------------- utilidades
def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return txt.strip().upper()


def num(valor, casas=None):
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or v < -900:  # o INPE usa -999 para "sem dado"
        return None
    return round(v, casas) if casas is not None else v


def ler_csv(conteudo: bytes) -> list[dict]:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = conteudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(texto)))


# ------------------------------------------------------------------ INPE
def baixar_inpe(dias: int) -> tuple[list[list], str | None]:
    estados = {normalizar(e) for e in CONFIG.get("estados", [])}
    excluir = {s.upper() for s in CONFIG.get("satelites_excluir", [])}
    hoje = datetime.now(timezone.utc).date()
    focos, ultimo_dia = [], None

    # o arquivo do dia D é publicado na manhã do dia D+1
    for delta in range(dias, 0, -1):
        dia = hoje - timedelta(days=delta)
        url = INPE_DIARIO.format(data=dia.strftime("%Y%m%d"))
        try:
            r = SESSAO.get(url, timeout=60)
        except requests.RequestException as e:
            print(f"  ! {dia}: falha de rede ({e})")
            continue
        if r.status_code != 200:
            print(f"  - {dia}: ainda não publicado (HTTP {r.status_code})")
            continue

        linhas = ler_csv(r.content)
        n0 = len(focos)
        for l in linhas:
            if estados and normalizar(l.get("estado")) not in estados:
                continue
            sat = (l.get("satelite") or "").strip()
            if sat.upper() in excluir:
                continue
            lat, lon = num(l.get("lat"), 5), num(l.get("lon"), 5)
            if lat is None or lon is None:
                continue
            t = (l.get("data_hora_gmt") or "").strip().replace(" ", "T")[:16] + "Z"
            focos.append([
                lat, lon, t, sat,
                (l.get("municipio") or "").strip().title(),
                (l.get("estado") or "").strip().title(),
                num(l.get("numero_dias_sem_chuva")),
                num(l.get("risco_fogo"), 2),
                num(l.get("frp"), 1),
                (l.get("bioma") or "").strip(),
                "INPE",
            ])
        ultimo_dia = dia.isoformat()
        print(f"  + {dia}: {len(focos) - n0} focos no recorte")
    return focos, ultimo_dia


# ------------------------------------------------------------------ FIRMS
def baixar_firms(ultimo_dia_inpe: str | None) -> list[list]:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        return []
    cfg = CONFIG.get("firms", {})
    bbox = ",".join(str(v) for v in cfg.get("bbox", []))
    url = FIRMS_AREA.format(key=key, fonte=cfg.get("fonte", "VIIRS_NOAA21_NRT"), bbox=bbox, dias=2)
    try:
        r = SESSAO.get(url, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! FIRMS indisponível ({e})")
        return []

    focos = []
    for l in ler_csv(r.content):
        dia = l.get("acq_date", "")
        # só o que o INPE ainda não publicou (dia corrente)
        if ultimo_dia_inpe and dia <= ultimo_dia_inpe:
            continue
        hhmm = (l.get("acq_time") or "0000").zfill(4)
        focos.append([
            num(l.get("latitude"), 5), num(l.get("longitude"), 5),
            f"{dia}T{hhmm[:2]}:{hhmm[2:]}Z",
            f"FIRMS {cfg.get('fonte', '')}",
            "", "", None, None, num(l.get("frp"), 1), "", "FIRMS",
        ])
    print(f"  + FIRMS (dia corrente): {len(focos)} focos")
    return focos


# ------------------------------------------------------------ geometria (m)
R_TERRA = 6_371_008.8


def para_metros(lon, lat, lat0):
    x = math.radians(lon) * R_TERRA * math.cos(math.radians(lat0))
    y = math.radians(lat) * R_TERRA
    return x, y


def dist_seg(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dentro(p, anel):
    x, y, ok = p[0], p[1], False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i]
        xj, yj = anel[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ok = not ok
        j = i
    return ok


def distancia_m(lon, lat, geom) -> float:
    """Distância em metros do ponto até a geometria (0 se estiver dentro)."""
    tipo, coords = geom["type"], geom["coordinates"]
    p = para_metros(lon, lat, lat)
    if tipo == "Point":
        return math.dist(p, para_metros(*coords, lat))
    poligonos = [coords] if tipo == "Polygon" else coords
    melhor = float("inf")
    for poli in poligonos:
        aneis = [[para_metros(x, y, lat) for x, y in anel] for anel in poli]
        if dentro(p, aneis[0]) and not any(dentro(p, h) for h in aneis[1:]):
            return 0.0
        for anel in aneis:
            for a, b in zip(anel, anel[1:]):
                melhor = min(melhor, dist_seg(p, a, b))
    return melhor


def calcular_alertas(focos: list[list]) -> list[dict]:
    arq = DATA / "areas_monitoradas.geojson"
    if not arq.exists():
        return []
    areas = json.loads(arq.read_text(encoding="utf-8"))["features"]
    alertas = []
    for f in areas:
        nome = f["properties"].get("nome", "Área")
        raio = float(f["properties"].get("raio_m", 2000))
        for foco in focos:
            d = distancia_m(foco[1], foco[0], f["geometry"])
            if d <= raio:
                alertas.append({
                    "id": f"{nome}|{foco[0]}|{foco[1]}|{foco[2]}",
                    "area": nome, "raio_m": raio, "distancia_m": round(d),
                    "lat": foco[0], "lon": foco[1], "t": foco[2],
                    "sat": foco[3], "mun": foco[4], "frp": foco[8],
                })
    alertas.sort(key=lambda a: a["t"], reverse=True)
    return alertas


# ---------------------------------------------------------------- Telegram
def notificar(alertas: list[dict]) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    estado_arq = DATA / "alertas_enviados.json"
    enviados = set(json.loads(estado_arq.read_text())) if estado_arq.exists() else set()
    novos = [a for a in alertas if a["id"] not in enviados]
    if not (token and chat and novos):
        return
    linhas = [f"🔥 {len(novos)} foco(s) novo(s) perto de áreas monitoradas"]
    for a in novos[:15]:
        linhas.append(
            f"• {a['area']}: {a['distancia_m']} m · {a['mun'] or '-'} · {a['t']} UTC · "
            f"https://maps.google.com/?q={a['lat']},{a['lon']}"
        )
    try:
        SESSAO.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(linhas), "disable_web_page_preview": True},
            timeout=30,
        ).raise_for_status()
        enviados |= {a["id"] for a in novos}
        estado_arq.write_text(json.dumps(sorted(enviados)[-5000:]))
        print(f"  ✓ Telegram: {len(novos)} alerta(s) enviados")
    except requests.RequestException as e:
        print(f"  ! Telegram falhou ({e})")


# ------------------------------------------------------------------- demo
def gerar_demo(dias: int) -> list[list]:
    random.seed(42)
    polos = [  # (município, lat, lon, peso, bioma)
        ("Ribeirão Preto", -21.18, -47.81, 9, "Cerrado"), ("Sertãozinho", -21.14, -47.99, 8, "Cerrado"),
        ("Barretos", -20.56, -48.57, 6, "Cerrado"), ("Araçatuba", -21.21, -50.43, 6, "Mata Atlântica"),
        ("Presidente Prudente", -22.12, -51.39, 5, "Mata Atlântica"), ("Botucatu", -22.88, -48.44, 5, "Cerrado"),
        ("São José do Rio Preto", -20.81, -49.38, 7, "Mata Atlântica"), ("Franca", -20.54, -47.40, 5, "Cerrado"),
        ("Bauru", -22.31, -49.06, 4, "Cerrado"), ("Itapeva", -23.98, -48.87, 3, "Mata Atlântica"),
        ("Piracicaba", -22.72, -47.65, 4, "Mata Atlântica"), ("Jaú", -22.29, -48.56, 5, "Mata Atlântica"),
        ("Assis", -22.66, -50.41, 3, "Mata Atlântica"), ("Marília", -22.21, -49.95, 3, "Mata Atlântica"),
        ("Campos do Jordão", -22.74, -45.59, 1, "Mata Atlântica"), ("Andradina", -20.90, -51.38, 4, "Mata Atlântica"),
    ]
    sats = ["NOAA-21", "NOAA-20", "NPP-375", "NPP-375D", "GOES-19", "AQUA_M-T"]
    fim = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    focos = []
    for d in range(dias):
        intensidade = 0.6 + 0.8 * (d / max(dias - 1, 1))  # tendência de alta
        for mun, lat, lon, peso, bioma in polos:
            for _ in range(int(random.gauss(peso * 4 * intensidade, 3)) if peso else 0):
                t = fim - timedelta(days=dias - 1 - d, hours=random.choice([3, 4, 15, 16, 17, 18]),
                                    minutes=random.randint(0, 59))
                seca = random.randint(5, 60)
                focos.append([
                    round(lat + random.gauss(0, 0.18), 5), round(lon + random.gauss(0, 0.2), 5),
                    t.strftime("%Y-%m-%dT%H:%MZ"), random.choices(sats, [5, 5, 3, 2, 4, 1])[0],
                    mun, "São Paulo", seca, round(min(1, seca / 50), 2),
                    round(random.lognormvariate(2.2, 0.9), 1), bioma, "DEMO",
                ])
    return focos


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="gera dados sintéticos")
    args = ap.parse_args()
    dias = int(CONFIG.get("dias", 7))
    DATA.mkdir(parents=True, exist_ok=True)

    if args.demo:
        print("Gerando dados de demonstração…")
        focos, ultimo = gerar_demo(dias), None
    else:
        print("Baixando focos do INPE…")
        focos, ultimo = baixar_inpe(dias)
        focos += baixar_firms(ultimo)
        if not focos and (DATA / "focos.json").exists():
            print("Nenhum dado novo baixado; mantendo a versão anterior.")
            return 0

    focos.sort(key=lambda f: f[2])
    alertas = calcular_alertas(focos)

    (DATA / "focos.json").write_text(
        json.dumps({"campos": CAMPOS, "dados": focos}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (DATA / "alertas.json").write_text(json.dumps(alertas, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "titulo": CONFIG.get("titulo"),
        "atualizado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "ultimo_dia_inpe": ultimo,
        "dias": dias,
        "estados": CONFIG.get("estados", []),
        "total_focos": len(focos),
        "total_alertas": len(alertas),
        "demo": args.demo,
        "mapa": CONFIG.get("mapa", {}),
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {len(focos)} focos · {len(alertas)} alertas gravados em docs/data/")

    if not args.demo:
        notificar(alertas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
