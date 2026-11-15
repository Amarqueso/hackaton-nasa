"""Baixa as tábuas de risco do ZARC (MAPA, dados abertos) e guarda só Brasília/DF.

Os CSVs nacionais têm centenas de MB; o app usa só o recorte do DF em data/zarc_brasilia.csv.
Rodar:  python scripts/atualizar_zarc.py            (safras padrão)
        python scripts/atualizar_zarc.py 2026-2027  (escolher safras)
Fonte: https://dados.agricultura.gov.br/dataset/tabua-de-risco-zoneamento-agricola-de-risco-climatico
"""
import csv, io, sys, pathlib, requests

GEOCODIGO_BRASILIA = "5300108"  # IBGE - Brasília (DF inteiro é um único município)
API = "https://dados.agricultura.gov.br/api/3/action/package_show?id=tabua-de-risco-zoneamento-agricola-de-risco-climatico"
PADRAO = ["2025-2026", "2026-2027"]
SAIDA = pathlib.Path(__file__).resolve().parent.parent / "data" / "zarc_brasilia.csv"


def urls(safras):
    res = requests.get(API, timeout=60).json()["result"]["resources"]
    out = {}
    for r in res:
        for s in safras:
            if r["url"].endswith(f"safra-{s}.csv"):
                out[s] = r["url"]
    return out


def linhas_df(url):
    """Lê o CSV em streaming e devolve só as linhas de Brasília."""
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        raw = r.raw
        raw.decode_content = True
        primeiro = raw.read(4096)
        try:
            primeiro.decode("utf-8"); enc = "utf-8-sig"
        except UnicodeDecodeError:
            enc = "latin-1"
        stream = io.TextIOWrapper(io.BufferedReader(_Junta(primeiro, raw)), encoding=enc, errors="replace", newline="")
        leitor = csv.reader(stream, delimiter=";")
        cab = next(leitor)
        i = cab.index("geocodigo")
        for row in leitor:
            if len(row) > i and row[i] == GEOCODIGO_BRASILIA:
                yield cab, row


class _Junta(io.RawIOBase):
    """Concatena os bytes já lidos com o resto do stream."""
    def __init__(self, head, rest):
        self.head, self.rest = head, rest
    def readable(self):
        return True
    def readinto(self, b):
        if self.head:
            n = min(len(b), len(self.head)); b[:n] = self.head[:n]; self.head = self.head[n:]; return n
        data = self.rest.read(len(b))
        n = len(data); b[:n] = data; return n


def main():
    safras = sys.argv[1:] or PADRAO
    alvo = urls(safras)
    faltando = set(safras) - set(alvo)
    if faltando:
        print("Safras não encontradas no portal:", ", ".join(sorted(faltando)))
    cab, linhas = None, []
    for s, u in alvo.items():
        print(f"Baixando safra {s}… (arquivo nacional grande, pode demorar)")
        n = 0
        for c, row in linhas_df(u):
            cab = c; linhas.append(row); n += 1
        print(f"  {n} linhas de Brasília")
    if not linhas:
        sys.exit("Nenhuma linha de Brasília encontrada.")
    SAIDA.parent.mkdir(exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(cab); w.writerows(linhas)
    print(f"Salvo em {SAIDA} ({len(linhas)} linhas)")


if __name__ == "__main__":
    main()
