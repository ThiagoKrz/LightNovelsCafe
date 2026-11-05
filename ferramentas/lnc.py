#!/usr/bin/env python3
"""
lnc.py: ferramenta de linha de comando do LightNovelsCafe.

Usa só a biblioteca padrão do Python 3.9+. Rode da raiz do repositório:

    python ferramentas/lnc.py <comando> [argumentos]
    (no Windows também dá para usar:  .\\lnc.cmd <comando>)

Comandos principais:

    status                      Situação de todas as obras/volumes.
    comparar  <volume>          Gera um HTML com original x tradução lado a lado.
    validar   <volume|todos>    Confere XML, estrutura, encoding, travessões, termos crus.
    preparar  <epub> <obra> <n> Cria o workbench de um volume novo a partir de um EPUB.
    sincronizar <volume>        Copia translation/translated-html -> package.
    montar    <volume>          Gera o EPUB em <volume>/build/ a partir de package/.
    publicar  <volume>          validar + sincronizar + montar + copiar para publicados/.

Comandos de manutenção:

    readmes   <volume|todos>    (Re)gera README.md dos volumes/obras e cria REVISAO.md.
    restaurar-originais <volume> <epub>
                                Recria original-html/plaintext que estiverem faltando.
    converter-manifest <volume|todos>
                                Converte manifest antigo (caminhos a partir da raiz) para
                                caminhos relativos à pasta do volume.

<volume> pode ser escrito como "fate-apocrypha/vol-1", "fate-apocrypha/1" ou o caminho
da pasta do volume.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html.entities import name2codepoint
from html.parser import HTMLParser
from pathlib import Path

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

REPO = Path(__file__).resolve().parents[1]
OBRAS = REPO / "obras"
PUBLICADOS = REPO / "publicados"
ORIGINAIS = REPO / "originais"
PADROES = REPO / "padroes"
GLOSSARIO = PADROES / "GLOSSARIO_TYPEMOON_OPERACIONAL.json"
REVISAO_HTML = REPO / "_revisao"

STATUS_INICIO = "<!-- STATUS:INICIO (gerado por `lnc status --atualizar-readme`; não edite à mão) -->"
STATUS_FIM = "<!-- STATUS:FIM -->"

HTML_TYPES = {"application/xhtml+xml", "text/html"}


# ---------------------------------------------------------------------------
# utilidades gerais
# ---------------------------------------------------------------------------

def erro(msg: str) -> "None":
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def ler_texto(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def escrever_texto(path: Path, conteudo: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(conteudo.encode("utf-8"))


def rel_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def html_para_texto_puro(conteudo: str) -> str:
    """Mesma conversão usada desde o script original (prepare-epub-workbench.ps1)."""
    t = re.sub(r"<script\b[^>]*>.*?</script>", "", conteudo, flags=re.I | re.S)
    t = re.sub(r"<style\b[^>]*>.*?</style>", "", t, flags=re.I | re.S)
    t = re.sub(r"(?i)<br\s*/?>", "\r\n", t)
    t = re.sub(r"(?i)</(p|div|section|article|header|footer|li|tr|h1|h2|h3|h4|h5|h6)>", "\r\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"(\r?\n\s*){3,}", "\r\n\r\n", t)
    return t.strip()


# ---------------------------------------------------------------------------
# extração de blocos de texto (para comparar / validar)
# ---------------------------------------------------------------------------

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "dt", "dd",
              "figcaption", "td", "th", "pre", "caption"}
FLUSH_TAGS = {"div", "section", "article", "body", "aside", "header", "footer", "nav", "figure"}
SKIP_TAGS = {"script", "style", "head", "rt", "rp"}


@dataclass
class Bloco:
    tag: str
    texto: str


class _Extrator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocos: list[Bloco] = []
        self.imagens: list[str] = []
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self._skip = 0
        self._depth = 0
        self._tag = ""
        self._buf: list[str] = []
        self._solto: list[str] = []

    def _flush_solto(self) -> None:
        texto = _normaliza("".join(self._solto))
        if texto:
            self.blocos.append(Bloco("texto", texto))
        self._solto = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        if tag in ("img", "image"):
            src = a.get("src") or a.get("xlink:href") or a.get("href") or ""
            self.imagens.append(src)
            nome = src.rsplit("/", 1)[-1]
            if self._depth:
                self._buf.append(f" [imagem {nome}] ")
            else:
                self._flush_solto()
                self.blocos.append(Bloco("img", f"[imagem {nome}]"))
            return
        if tag == "br":
            (self._buf if self._depth else self._solto).append("\n")
            return
        if tag in BLOCK_TAGS:
            if self._depth == 0:
                self._flush_solto()
                self._tag = tag
            self._depth += 1
        elif tag in FLUSH_TAGS and self._depth == 0:
            self._flush_solto()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in BLOCK_TAGS or tag in SKIP_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in BLOCK_TAGS and self._depth:
            self._depth -= 1
            if self._depth == 0:
                texto = _normaliza("".join(self._buf))
                if texto:
                    self.blocos.append(Bloco(self._tag, texto))
                self._buf = []
        elif tag in FLUSH_TAGS and self._depth == 0:
            self._flush_solto()

    def handle_data(self, data):
        if self._skip:
            return
        (self._buf if self._depth else self._solto).append(data)

    def close(self):
        super().close()
        if self._buf:
            texto = _normaliza("".join(self._buf))
            if texto:
                self.blocos.append(Bloco(self._tag or "texto", texto))
        self._flush_solto()


def _normaliza(texto: str) -> str:
    return re.sub(r"[ \t\r\n\u00a0]+", " ", texto).strip()


def extrair(conteudo: str) -> _Extrator:
    ex = _Extrator()
    ex.feed(conteudo)
    ex.close()
    return ex


def texto_util(ex: _Extrator) -> str:
    raw = " ".join(b.texto for b in ex.blocos if b.tag != "img")
    return re.sub(r"\[imagem\s+[^\]]+\]", "", raw).strip()


# ---------------------------------------------------------------------------
# volumes
# ---------------------------------------------------------------------------

class Volume:
    def __init__(self, diretorio: Path) -> None:
        self.dir = diretorio.resolve()
        self.manifest_path = self.dir / "manifest.json"
        if not self.manifest_path.exists():
            erro(f"manifest.json não encontrado em {rel_repo(self.dir)}")
        self.manifest = json.loads(ler_texto(self.manifest_path))
        self.slug = self.dir.parent.name
        m = re.match(r"vol-(\d+)$", self.dir.name)
        self.numero = int(m.group(1)) if m else 0

    # -- identificação -----------------------------------------------------
    @property
    def id(self) -> str:
        return f"{self.slug}/vol-{self.numero}"

    @property
    def serie(self) -> dict:
        return carregar_serie(self.slug)

    @property
    def titulo(self) -> str:
        return f"{self.serie.get('titulo', self.slug)}, Vol. {self.numero}"

    # -- caminhos ----------------------------------------------------------
    @property
    def legado(self) -> bool:
        return self.manifest.get("pathBase") != "volume"

    def caminho(self, rel: str | None) -> Path | None:
        if not rel:
            return None
        rel = rel.replace("\\", "/")
        if not self.legado:
            return (self.dir / rel).resolve()
        if rel.startswith("novel-workbench/"):
            rel = "obras/" + rel[len("novel-workbench/"):]
        return (REPO / rel).resolve()

    def relativo(self, path: Path) -> str:
        return Path(os.path.relpath(path, self.dir)).as_posix()

    @property
    def package_root(self) -> Path:
        return self.caminho(self.manifest["packageRoot"])

    @property
    def entradas(self) -> list[dict]:
        return [e for e in self.manifest.get("spine", []) if e.get("translationWorkingPath")]

    def original(self, e: dict) -> Path:
        return self.caminho(e["translationOriginalPath"])

    def traduzido(self, e: dict) -> Path:
        return self.caminho(e["translationWorkingPath"])

    def pacote(self, e: dict) -> Path:
        return self.caminho(e["packagePath"])

    def plaintext(self, e: dict) -> Path | None:
        return self.caminho(e.get("plainTextPath"))

    @property
    def nome_publicacao(self) -> str:
        return f"{self.slug}-vol-{self.numero}-pt-br.epub"

    @property
    def build_path(self) -> Path:
        return self.dir / "build" / self.nome_publicacao

    @property
    def publicado_path(self) -> Path:
        return PUBLICADOS / self.slug / self.nome_publicacao

    @property
    def origem_path(self) -> Path | None:
        p = self.caminho(self.manifest.get("sourceEpub"))
        return p if p and p.exists() else None

    def salvar_manifest(self) -> None:
        escrever_texto(self.manifest_path, json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n")


def carregar_serie(slug: str) -> dict:
    p = OBRAS / slug / "serie.json"
    return json.loads(ler_texto(p)) if p.exists() else {"titulo": slug}


def todos_volumes() -> list[Volume]:
    vols = [Volume(p.parent) for p in OBRAS.glob("*/vol-*/manifest.json")]
    return sorted(vols, key=lambda v: (v.slug, v.numero))


def achar_volume(arg: str) -> Volume:
    p = Path(arg)
    for cand in (p, REPO / p):
        if cand.is_file() and cand.name == "manifest.json":
            return Volume(cand.parent)
        if (cand / "manifest.json").is_file():
            return Volume(cand)
    m = re.match(r"^(?:obras[/\\])?([\w.-]+)[/\\ ](?:vol-?)?(\d+)[/\\]?$", arg.strip())
    if m:
        d = OBRAS / m.group(1) / f"vol-{int(m.group(2))}"
        if (d / "manifest.json").is_file():
            return Volume(d)
    disponiveis = ", ".join(v.id for v in todos_volumes())
    erro(f"volume '{arg}' não encontrado. Disponíveis: {disponiveis}")


def volumes_do_arg(arg: str) -> list[Volume]:
    if arg in ("todos", "all", "*"):
        return todos_volumes()
    if (OBRAS / arg).is_dir() and not (OBRAS / arg / "manifest.json").exists():
        return [v for v in todos_volumes() if v.slug == arg]
    return [achar_volume(arg)]


# ---------------------------------------------------------------------------
# progresso de tradução e revisão
# ---------------------------------------------------------------------------

@dataclass
class Progresso:
    com_texto: int = 0
    traduzidos: int = 0
    sem_original: int = 0
    revisados: int = 0
    itens_revisao: int = 0


def arquivo_traduzido(vol: Volume, e: dict) -> bool | None:
    """True/False quando há original para comparar; None quando não há."""
    orig, trad = vol.original(e), vol.traduzido(e)
    if not trad.exists():
        return False
    if not orig.exists():
        return None
    return texto_util(extrair(ler_texto(orig))) != texto_util(extrair(ler_texto(trad)))


def progresso(vol: Volume) -> Progresso:
    pr = Progresso()
    for e in vol.entradas:
        orig = vol.original(e)
        base = orig if orig.exists() else vol.traduzido(e)
        if not base.exists() or not texto_util(extrair(ler_texto(base))):
            continue
        pr.com_texto += 1
        r = arquivo_traduzido(vol, e)
        if r is None:
            pr.sem_original += 1
            pr.traduzidos += 1
        elif r:
            pr.traduzidos += 1
    rev = vol.dir / "REVISAO.md"
    if rev.exists():
        linhas = re.findall(r"^\s*- \[( |x|X)\]", ler_texto(rev), flags=re.M)
        pr.itens_revisao = len(linhas)
        pr.revisados = sum(1 for x in linhas if x.lower() == "x")
    return pr


def situacao(vol: Volume, pr: Progresso) -> str:
    publicado = vol.publicado_path.exists()
    if pr.com_texto and pr.traduzidos >= pr.com_texto:
        return "✅ Publicado" if publicado else "🟦 Traduzido (falta publicar)"
    if pr.traduzidos == 0:
        return "⬜ Preparado (tradução não iniciada)"
    return f"🟨 Em tradução ({pr.traduzidos}/{pr.com_texto} arquivos)"


def linha_revisao(pr: Progresso) -> str:
    if not pr.itens_revisao:
        return "-"
    pct = round(100 * pr.revisados / pr.itens_revisao)
    return f"{pr.revisados}/{pr.itens_revisao} ({pct}%)"


# ---------------------------------------------------------------------------
# comando: status
# ---------------------------------------------------------------------------

def tabela_status(vols: list[Volume], com_obra: bool = True) -> str:
    cab = ("| Obra | Vol. | Situação | Revisão | EPUB publicado |\n|---|---|---|---|---|\n" if com_obra
           else "| Vol. | Situação | Revisão | EPUB publicado |\n|---|---|---|---|\n")
    linhas = []
    for v in vols:
        pr = progresso(v)
        pub = f"[{v.nome_publicacao}]({rel_repo(v.publicado_path)})" if v.publicado_path.exists() else "-"
        vol_link = f"[{v.numero}]({rel_repo(v.dir)})"
        celulas = [vol_link, situacao(v, pr), linha_revisao(pr), pub]
        if com_obra:
            celulas.insert(0, v.serie.get("titulo", v.slug))
        linhas.append("| " + " | ".join(celulas) + " |")
    return cab + "\n".join(linhas) + "\n"


def substituir_bloco_status(path: Path, conteudo: str, base: Path) -> bool:
    if not path.exists():
        return False
    texto = ler_texto(path)
    if STATUS_INICIO not in texto or STATUS_FIM not in texto:
        return False
    # links da tabela são gerados relativos à raiz; ajusta para a pasta do README
    if base != REPO:
        prefixo = Path(os.path.relpath(REPO, base)).as_posix() + "/"
        conteudo = re.sub(r"\]\((?!http)([^)]+)\)", lambda m: f"]({prefixo}{m.group(1)})", conteudo)
    ini = texto.index(STATUS_INICIO) + len(STATUS_INICIO)
    fim = texto.index(STATUS_FIM)
    novo = texto[:ini] + "\n\n" + conteudo + "\n" + texto[fim:]
    if novo != texto:
        escrever_texto(path, novo)
    return True


def cmd_status(args) -> None:
    vols = todos_volumes()
    if args.obra:
        vols = [v for v in vols if v.slug == args.obra]
    if args.atualizar_readme:
        substituir_bloco_status(REPO / "README.md", tabela_status(vols), REPO)
        for slug in sorted({v.slug for v in vols}):
            serie_dir = OBRAS / slug
            substituir_bloco_status(serie_dir / "README.md",
                                    tabela_status([v for v in vols if v.slug == slug], com_obra=False), serie_dir)
        print("Tabelas de status atualizadas nos README.md.")
        return
    if args.markdown:
        print(tabela_status(vols))
        return
    larg = max((len(v.id) for v in vols), default=10)
    print(f"{'VOLUME'.ljust(larg)}  {'SITUAÇÃO'.ljust(40)}  REVISÃO")
    for v in vols:
        pr = progresso(v)
        extra = f"  (sem original-html em {pr.sem_original} arq.)" if pr.sem_original else ""
        print(f"{v.id.ljust(larg)}  {situacao(v, pr).ljust(40)}  {linha_revisao(pr)}{extra}")


# ---------------------------------------------------------------------------
# comando: validar
# ---------------------------------------------------------------------------

@dataclass
class Achado:
    nivel: str  # "ERRO" | "AVISO"
    arquivo: str
    msg: str


RE_MOJIBAKE = re.compile(r"Ã[\u0080-\u00bf]|Â[\u0080-\u00bf]|â€|\ufffd")
RE_INTERROGACAO = re.compile(r"(?<=[A-Za-zÀ-ÿ])\?(?=[A-Za-zÀ-ÿ])")
RE_JAPONES = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
RE_INGLES = re.compile(r"\b(the|and|you|that|was|with|have|this|what|his|her|they)\b", re.I)
RE_TRAVESSAO = re.compile(r"^[—―–]\s*\S")


def checar_xml(conteudo: str) -> str | None:
    def ent(m: re.Match) -> str:
        nome = m.group(1)
        if nome in ("amp", "lt", "gt", "quot", "apos"):
            return m.group(0)
        return f"&#{name2codepoint[nome]};" if nome in name2codepoint else m.group(0)

    t = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", ent, conteudo)
    try:
        ET.fromstring(t.encode("utf-8"))
    except ET.ParseError as ex:
        return str(ex)
    return None


def termos_glossario(slug: str) -> tuple[dict[str, str], list[str]]:
    """Retorna (termo_em_ingles -> forma_ptbr) e formas proibidas (marcadas como ERRO)."""
    if not GLOSSARIO.exists():
        return {}, []
    g = json.loads(ler_texto(GLOSSARIO))
    tokens = [t for t in slug.split("-") if len(t) > 3 and t not in ("fate", "case", "files")]
    traduzir: dict[str, str] = {}
    proibidos: list[str] = []
    for secao, valor in g.items():
        if not secao.startswith("glossario") or not isinstance(valor, dict):
            continue
        if "geral" not in secao and not any(t in secao for t in tokens):
            continue
        for termo, forma in valor.items():
            if termo.startswith("_") or not isinstance(forma, str):
                continue
            if forma.upper().startswith("ERRO"):
                proibidos.append(termo)
            elif (forma != termo and termo[:1].isupper() and len(termo) >= 4
                  and termo.lower() not in forma.lower() and re.fullmatch(r"[A-Za-z' -]+", termo)):
                traduzir[termo] = forma
    return traduzir, proibidos


def validar_volume(vol: Volume) -> list[Achado]:
    achados: list[Achado] = []
    traduzir, proibidos = termos_glossario(vol.slug)
    termos_crus: Counter = Counter()
    proibidos_achados: Counter = Counter()
    re_termos = re.compile(r"\b(" + "|".join(sorted(map(re.escape, traduzir), key=len, reverse=True)) + r")\b") if traduzir else None
    re_proib = re.compile(r"\b(" + "|".join(map(re.escape, proibidos)) + r")\b") if proibidos else None

    for e in vol.entradas:
        trad = vol.traduzido(e)
        nome = trad.name
        if not trad.exists():
            achados.append(Achado("ERRO", nome, "arquivo de tradução não existe"))
            continue
        conteudo = ler_texto(trad)
        orig_path = vol.original(e)
        orig_conteudo = ler_texto(orig_path) if orig_path.exists() else None

        problema = checar_xml(conteudo)
        if problema:
            if orig_conteudo is not None and checar_xml(orig_conteudo):
                achados.append(Achado("AVISO", nome, f"XML inválido (o original também era): {problema}"))
            else:
                achados.append(Achado("ERRO", nome, f"XML inválido: {problema}"))

        ex_t = extrair(conteudo)
        traduzido_de_fato = True
        if orig_conteudo is not None:
            ex_o = extrair(orig_conteudo)
            for rotulo, a, b in (("imagens", ex_o.imagens, ex_t.imagens), ("ids", ex_o.ids, ex_t.ids),
                                 ("links", ex_o.hrefs, ex_t.hrefs)):
                faltando = Counter(a) - Counter(b)
                sobrando = Counter(b) - Counter(a)
                if faltando:
                    achados.append(Achado("ERRO", nome, f"{rotulo} do original sumiram: {', '.join(list(faltando)[:5])}"))
                if sobrando:
                    achados.append(Achado("AVISO", nome, f"{rotulo} novos que não existem no original: {', '.join(list(sobrando)[:5])}"))
            traduzido_de_fato = texto_util(ex_o) != texto_util(ex_t)
            if traduzido_de_fato:
                originais = {b.texto for b in ex_o.blocos}
                for b in ex_t.blocos:
                    if (b.texto in originais and len(b.texto) > 40
                            and len(RE_INGLES.findall(b.texto)) >= 3):
                        achados.append(Achado("AVISO", nome, f"parágrafo parece não traduzido: \"{b.texto[:90]}…\""))

        if not traduzido_de_fato:
            continue  # arquivo ainda não traduzido: não faz sentido checar estilo

        for b in ex_t.blocos:
            for m in RE_MOJIBAKE.finditer(b.texto):
                achados.append(Achado("ERRO", nome, f"possível encoding corrompido: \"…{b.texto[max(0, m.start()-25):m.end()+25]}…\""))
                break
            for m in RE_INTERROGACAO.finditer(b.texto):
                achados.append(Achado("ERRO", nome, f"'?' no meio de palavra (acento perdido?): \"…{b.texto[max(0, m.start()-25):m.end()+25]}…\""))
                break
            if RE_TRAVESSAO.match(b.texto):
                achados.append(Achado("AVISO", nome, f"fala com travessão (o padrão é aspas duplas): \"{b.texto[:80]}\""))
            if re_termos:
                termos_crus.update(re_termos.findall(b.texto))
            if re_proib:
                proibidos_achados.update(re_proib.findall(b.texto))
        jp = len(RE_JAPONES.findall(texto_util(ex_t)))
        if jp:
            achados.append(Achado("AVISO", nome, f"{jp} caractere(s) japoneses no texto traduzido (fora de ruby)"))

    for termo, n in proibidos_achados.most_common():
        achados.append(Achado("AVISO", "(volume)", f"forma marcada como ERRO no glossário: '{termo}' ({n}x)"))
    for termo, n in termos_crus.most_common(15):
        achados.append(Achado("AVISO", "(volume)", f"termo em inglês com forma PT-BR no glossário: '{termo}' -> '{traduzir[termo]}' ({n}x). Confira o contexto."))
    return achados


def cmd_validar(args) -> None:
    total_erros = 0
    for vol in volumes_do_arg(args.volume):
        achados = validar_volume(vol)
        erros = [a for a in achados if a.nivel == "ERRO"]
        avisos = [a for a in achados if a.nivel == "AVISO"]
        total_erros += len(erros)
        print(f"\n=== {vol.id}: {len(erros)} erro(s), {len(avisos)} aviso(s)")
        limite = None if args.tudo else 12
        for grupo in (erros, avisos):
            for a in grupo[:limite]:
                print(f"  [{a.nivel}] {a.arquivo}: {a.msg}")
            if limite and len(grupo) > limite:
                print(f"  ... e mais {len(grupo) - limite}. Use --tudo para ver todos.")
    print()
    if total_erros:
        print(f"Total: {total_erros} erro(s). Corrija antes de publicar.")
        sys.exit(1)
    print("Nenhum erro encontrado.")


# ---------------------------------------------------------------------------
# comando: comparar
# ---------------------------------------------------------------------------

CSS_COMPARAR = """
body{font-family:Georgia,serif;margin:0;background:#f4f1ea;color:#222}
header{position:sticky;top:0;background:#2b2118;color:#fff;padding:10px 20px;z-index:2}
header a{color:#ffd58a}
h2{font-family:system-ui,sans-serif;font-size:15px;background:#e2d9c8;margin:28px 0 0;padding:8px 20px}
table{width:100%;border-collapse:collapse;table-layout:fixed}
td{vertical-align:top;padding:6px 14px;border-bottom:1px solid #e0d8c8;line-height:1.5}
td.n{width:42px;color:#999;font:12px system-ui;text-align:right}
td.o{color:#555;background:#faf8f3}
tr.igual td.t{background:#fff3c4}
tr.falta td{background:#ffe0e0}
.tag{font:11px system-ui;color:#a08f70;margin-right:6px}
.aviso{background:#ffe0e0;padding:6px 20px;font:13px system-ui}
nav{padding:10px 20px;font:13px system-ui;columns:3}
nav a{display:block;color:#5a3d1e}
"""


def cmd_comparar(args) -> None:
    vol = achar_volume(args.volume)
    selecionados = set()
    if args.arquivo:
        for parte in args.arquivo.split(","):
            if "-" in parte:
                a, b = parte.split("-", 1)
                selecionados.update(range(int(a), int(b) + 1))
            else:
                selecionados.add(int(parte))
    partes = [f"<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>{html.escape(vol.titulo)}: comparação</title>",
              f"<style>{CSS_COMPARAR}</style><header><b>{html.escape(vol.titulo)}</b>: original (esq.) × tradução (dir.). "
              "Linhas em <span style='background:#fff3c4;color:#222'>amarelo</span> estão iguais ao original (possivelmente não traduzidas). "
              "Para corrigir, edite o arquivo em <code>translation/translated-html/</code>.</header>"]
    indice, corpo = [], []
    for i, e in enumerate(vol.entradas, start=1):
        if selecionados and i not in selecionados:
            continue
        trad, orig = vol.traduzido(e), vol.original(e)
        bt = extrair(ler_texto(trad)).blocos if trad.exists() else []
        bo = extrair(ler_texto(orig)).blocos if orig.exists() else []
        if not bt and not bo:
            continue
        ancora = f"a{i}"
        indice.append(f"<a href='#{ancora}'>{html.escape(trad.name)}</a>")
        corpo.append(f"<h2 id='{ancora}'>{html.escape(trad.name)}</h2>")
        if not orig.exists():
            corpo.append("<div class='aviso'>Sem original-html para este arquivo; mostrando só a tradução.</div>")
        elif len(bo) != len(bt):
            corpo.append(f"<div class='aviso'>Quantidade de parágrafos diferente (original {len(bo)} × tradução {len(bt)}); "
                         "o alinhamento abaixo é aproximado.</div>")
        linhas = _alinhar(bo, bt)
        corpo.append("<table>")
        for n, (o, t) in enumerate(linhas, start=1):
            classe = ""
            if o and t and o.texto == t.texto and o.tag != "img" and len(o.texto) > 3:
                classe = "igual"
            elif (o is None) != (t is None):
                classe = "falta"
            fo = f"<span class='tag'>{o.tag}</span>{html.escape(o.texto)}" if o else ""
            ft = f"<span class='tag'>{t.tag}</span>{html.escape(t.texto)}" if t else ""
            corpo.append(f"<tr class='{classe}'><td class='n'>{n}</td><td class='o'>{fo}</td><td class='t'>{ft}</td></tr>")
        corpo.append("</table>")
    partes.append("<nav>" + "".join(indice) + "</nav>")
    partes.extend(corpo)
    REVISAO_HTML.mkdir(exist_ok=True)
    sufixo = f"-arquivos-{args.arquivo.replace(',', '_')}" if args.arquivo else ""
    saida = REVISAO_HTML / f"{vol.slug}-vol-{vol.numero}{sufixo}.html"
    escrever_texto(saida, "\n".join(partes))
    print(f"Comparação gerada: {rel_repo(saida)}")
    if args.abrir:
        webbrowser.open(saida.as_uri())


def _alinhar(bo: list[Bloco], bt: list[Bloco]) -> list[tuple[Bloco | None, Bloco | None]]:
    if len(bo) == len(bt) or not bo or not bt:
        n = max(len(bo), len(bt))
        return [(bo[i] if i < len(bo) else None, bt[i] if i < len(bt) else None) for i in range(n)]
    # alinha pela "forma" (tag + imagens + tamanho aproximado) quando a contagem diverge
    sig = lambda b: b.tag if b.tag != "img" else b.texto
    sm = difflib.SequenceMatcher(a=[sig(b) for b in bo], b=[sig(b) for b in bt], autojunk=False)
    out: list[tuple[Bloco | None, Bloco | None]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            out.extend(zip(bo[i1:i2], bt[j1:j2]))
        else:
            a, b = bo[i1:i2], bt[j1:j2]
            for k in range(max(len(a), len(b))):
                out.append((a[k] if k < len(a) else None, b[k] if k < len(b) else None))
    return out


# ---------------------------------------------------------------------------
# EPUB: abrir, preparar, sincronizar, montar
# ---------------------------------------------------------------------------

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "ocf": "urn:oasis:names:tc:opendocument:xmlns:container"}


def achar_opf(package_dir: Path) -> Path:
    container = package_dir / "META-INF" / "container.xml"
    if container.exists():
        raiz = ET.fromstring(container.read_bytes()).find(".//ocf:rootfile", NS)
        if raiz is not None and raiz.get("full-path"):
            p = package_dir / raiz.get("full-path")
            if p.exists():
                return p
    for p in [package_dir / "content.opf", *package_dir.rglob("*.opf")]:
        if p.exists():
            return p
    erro(f"nenhum .opf encontrado em {package_dir}")


def ler_spine(package_dir: Path) -> tuple[Path, ET.Element, list[dict]]:
    opf_path = achar_opf(package_dir)
    opf = ET.fromstring(opf_path.read_bytes())
    itens = {i.get("id"): i for i in opf.findall(".//opf:manifest/opf:item", NS)}
    spine = []
    for ordem, ref in enumerate(opf.findall(".//opf:spine/opf:itemref", NS), start=1):
        item = itens.get(ref.get("idref"))
        if item is None:
            continue
        spine.append({"order": ordem, "idref": ref.get("idref"), "href": item.get("href"),
                      "mediaType": item.get("media-type")})
    return opf_path, opf, spine


def cmd_preparar(args) -> None:
    epub = Path(args.epub).resolve()
    if not epub.exists():
        erro(f"EPUB não encontrado: {epub}")
    slug, numero = args.obra, int(args.numero)
    vol_dir = OBRAS / slug / f"vol-{numero}"
    if vol_dir.exists():
        if not args.forcar:
            erro(f"{rel_repo(vol_dir)} já existe. Use --forcar para recriar (APAGA o trabalho existente).")
        shutil.rmtree(vol_dir)

    destino_origem = ORIGINAIS / slug / f"vol-{numero}{epub.suffix.lower()}"
    if epub != destino_origem.resolve():
        destino_origem.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(epub, destino_origem)
        print(f"Original guardado em {rel_repo(destino_origem)}")

    package = vol_dir / "package"
    orig_dir = vol_dir / "translation" / "original-html"
    trad_dir = vol_dir / "translation" / "translated-html"
    txt_dir = vol_dir / "translation" / "plaintext"
    for d in (package, orig_dir, trad_dir, txt_dir, vol_dir / "build"):
        d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino_origem) as z:
        z.extractall(package)

    opf_path, opf, spine = ler_spine(package)
    opf_dir = opf_path.parent
    n_html = 0
    for e in spine:
        alvo = (opf_dir / e["href"]).resolve()
        e["packagePath"] = Path(os.path.relpath(alvo, vol_dir)).as_posix()
        if e["mediaType"] in HTML_TYPES and alvo.exists():
            n_html += 1
            nome = f"{n_html:03d}-{alvo.name}"
            conteudo = ler_texto(alvo)
            texto = html_para_texto_puro(conteudo)
            shutil.copy2(alvo, orig_dir / nome)
            shutil.copy2(alvo, trad_dir / nome)
            escrever_texto(txt_dir / f"{n_html:03d}-{alvo.stem}.txt", texto)
            e["translationOriginalPath"] = f"translation/original-html/{nome}"
            e["translationWorkingPath"] = f"translation/translated-html/{nome}"
            e["plainTextPath"] = f"translation/plaintext/{n_html:03d}-{alvo.stem}.txt"
            e["plainTextLength"] = len(texto)

    def dc(tag: str) -> str:
        el = opf.find(f".//opf:metadata/dc:{tag}", NS)
        return (el.text or "").strip() if el is not None else ""

    itens = opf.findall(".//opf:manifest/opf:item", NS)
    spine_el = opf.find(".//opf:spine", NS)
    manifest = {
        "pathBase": "volume",
        "obra": slug,
        "volume": numero,
        "title": dc("title"),
        "author": dc("creator"),
        "language": dc("language"),
        "publisher": dc("publisher"),
        "releaseDate": dc("date"),
        "pageProgressionDirection": spine_el.get("page-progression-direction", "") if spine_el is not None else "",
        "sourceEpub": Path(os.path.relpath(destino_origem, vol_dir)).as_posix(),
        "packageRoot": "package",
        "packageOpfPath": Path(os.path.relpath(opf_path, vol_dir)).as_posix(),
        "buildRoot": "build",
        "translation": {"originalHtmlRoot": "translation/original-html",
                        "translatedHtmlRoot": "translation/translated-html",
                        "plainTextRoot": "translation/plaintext"},
        "assets": {"imageCount": sum(1 for i in itens if (i.get("media-type") or "").startswith("image/")),
                   "cssFiles": [Path(os.path.relpath(opf_dir / i.get("href"), vol_dir)).as_posix()
                                for i in itens if i.get("media-type") == "text/css"]},
        "spine": spine,
    }
    escrever_texto(vol_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    serie_path = OBRAS / slug / "serie.json"
    if not serie_path.exists():
        escrever_texto(serie_path, json.dumps({"titulo": args.titulo or manifest["title"] or slug,
                                               "autor": args.autor or manifest["author"],
                                               "idiomaOrigem": manifest["language"] or "en",
                                               "observacoes": ""}, ensure_ascii=False, indent=2) + "\n")
        print(f"Criado {rel_repo(serie_path)}. Revise título/autor.")
    vol = Volume(vol_dir)
    gerar_readmes(vol, criar_revisao=True)
    gerar_readme_serie(slug)
    print(f"Volume preparado em {rel_repo(vol_dir)}: {n_html} arquivo(s) de texto, "
          f"{manifest['assets']['imageCount']} imagem(ns).")
    print("Próximo passo: leia docs/GUIA_DO_TRADUTOR.md (triagem de termos -> etapas de tradução).")


def cmd_restaurar_originais(args) -> None:
    vol = achar_volume(args.volume)
    epub = Path(args.epub).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(epub) as z:
            z.extractall(tmp)
        opf_path, _, _ = ler_spine(tmp)
        restaurados = 0
        for e in vol.entradas:
            fonte = (opf_path.parent / e["href"]).resolve()
            if not fonte.exists():
                print(f"  aviso: {e['href']} não existe no EPUB informado")
                continue
            orig = vol.original(e)
            if args.forcar or not orig.exists():
                orig.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fonte, orig)
                restaurados += 1
            txt = vol.plaintext(e)
            if txt is None:
                if vol.legado:
                    erro("rode `lnc converter-manifest` neste volume antes de restaurar originais.")
                stem = Path(e["translationWorkingPath"]).stem
                e["plainTextPath"] = f"translation/plaintext/{stem}.txt"
                txt = vol.plaintext(e)
            if args.forcar or not txt.exists():
                texto = html_para_texto_puro(ler_texto(fonte))
                escrever_texto(txt, texto)
                e["plainTextLength"] = len(texto)
        vol.salvar_manifest()
    print(f"{vol.id}: {restaurados} arquivo(s) original-html restaurado(s) a partir de {epub.name}.")


def sincronizar(vol: Volume) -> int:
    n = 0
    for e in vol.entradas:
        trad, pac = vol.traduzido(e), vol.pacote(e)
        dados = trad.read_bytes()
        if dados.startswith(b"\xef\xbb\xbf"):
            dados = dados[3:]
        if not dados.strip():
            erro(f"arquivo de tradução vazio: {rel_repo(trad)}")
        if not pac.exists() or pac.read_bytes() != dados:
            pac.write_bytes(dados)
            n += 1
    return n


def montar(vol: Volume, saida: Path | None = None) -> Path:
    pkg = vol.package_root
    mimetype = pkg / "mimetype"
    if not mimetype.exists():
        erro(f"pacote sem arquivo 'mimetype': {rel_repo(pkg)}")
    saida = saida or vol.build_path
    saida.parent.mkdir(parents=True, exist_ok=True)
    arquivos = sorted((p for p in pkg.rglob("*") if p.is_file() and p != mimetype
                       and p.name not in ("Thumbs.db", ".DS_Store", "desktop.ini")),
                      key=lambda p: p.relative_to(pkg).as_posix())
    with zipfile.ZipFile(saida, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mimetype.read_bytes(), compress_type=zipfile.ZIP_STORED)
        for p in arquivos:
            z.write(p, p.relative_to(pkg).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    return saida


def cmd_sincronizar(args) -> None:
    for vol in volumes_do_arg(args.volume):
        print(f"{vol.id}: {sincronizar(vol)} arquivo(s) atualizado(s) em package/.")


def cmd_montar(args) -> None:
    for vol in volumes_do_arg(args.volume):
        print(f"{vol.id}: EPUB gerado em {rel_repo(montar(vol))}")


def cmd_publicar(args) -> None:
    vol = achar_volume(args.volume)
    achados = validar_volume(vol)
    erros = [a for a in achados if a.nivel == "ERRO"]
    if erros and not args.forcar:
        for a in erros[:20]:
            print(f"  [ERRO] {a.arquivo}: {a.msg}")
        erro(f"{len(erros)} erro(s) de validação. Corrija ou use --forcar.")
    print(f"Sincronizados: {sincronizar(vol)} arquivo(s).")
    build = montar(vol)
    print(f"Montado: {rel_repo(build)}")
    vol.publicado_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build, vol.publicado_path)
    print(f"Publicado: {rel_repo(vol.publicado_path)}")
    substituir_bloco_status(REPO / "README.md", tabela_status(todos_volumes()), REPO)
    serie_dir = OBRAS / vol.slug
    substituir_bloco_status(serie_dir / "README.md",
                            tabela_status([v for v in todos_volumes() if v.slug == vol.slug], com_obra=False), serie_dir)
    print("Para colocar no site: copie o EPUB de publicados/ para src/assets/... do repositório do Fate Café.")


# ---------------------------------------------------------------------------
# manutenção: converter manifest, gerar READMEs
# ---------------------------------------------------------------------------

def cmd_converter_manifest(args) -> None:
    for vol in volumes_do_arg(args.volume):
        if not vol.legado:
            print(f"{vol.id}: já está no formato novo.")
            continue
        m = vol.manifest

        def conv(p):
            if not p:
                return p
            if isinstance(p, list):
                return [conv(x) for x in p]
            return vol.relativo(vol.caminho(p))

        novo = {"pathBase": "volume", "obra": vol.slug, "volume": vol.numero}
        for k, v in m.items():
            if k in ("workRoot",):
                continue
            if k in ("packageRoot", "packageOpfPath", "buildRoot"):
                novo[k] = conv(v)
            elif k == "sourceEpub":
                candidatos = sorted((ORIGINAIS / vol.slug).glob(f"vol-{vol.numero}.*")) if (ORIGINAIS / vol.slug).exists() else []
                novo["sourceEpub"] = vol.relativo(candidatos[0]) if candidatos else None
                novo["sourceEpubOriginal"] = v
            elif k == "translation":
                novo[k] = {kk: conv(vv) for kk, vv in v.items()}
            elif k == "assets":
                a = dict(v)
                css = a.get("cssFiles")
                a["cssFiles"] = conv(css) if isinstance(css, (str, list)) and css else []
                if isinstance(a["cssFiles"], str):
                    a["cssFiles"] = [a["cssFiles"]]
                if "illustrationFiles" in a:
                    a["illustrationFiles"] = conv(a["illustrationFiles"])
                novo[k] = a
            elif k == "spine":
                spine = []
                for e in v:
                    e = dict(e)
                    for kk in ("packagePath", "translationOriginalPath", "translationWorkingPath", "plainTextPath"):
                        if e.get(kk):
                            e[kk] = conv(e[kk])
                    spine.append(e)
                novo[k] = spine
            else:
                novo[k] = v
        if "packageRoot" not in novo:
            novo["packageRoot"] = "package"
        vol.manifest = novo
        vol.salvar_manifest()
        print(f"{vol.id}: manifest convertido.")


def _lista_arquivos_com_texto(vol: Volume) -> list[str]:
    nomes = []
    for e in vol.entradas:
        base = vol.original(e) if vol.original(e).exists() else vol.traduzido(e)
        if base.exists() and texto_util(extrair(ler_texto(base))):
            nomes.append(vol.traduzido(e).name)
    return nomes


def gerar_readmes(vol: Volume, criar_revisao: bool = True) -> None:
    serie = vol.serie
    idioma = {"ja": "japonês", "en": "inglês"}.get(serie.get("idiomaOrigem", ""), serie.get("idiomaOrigem", "?"))
    notas = vol.dir / "NOTAS_DE_ADAPTACAO.md"
    origem = vol.origem_path
    linhas = [
        f"# {vol.titulo}",
        "",
        "> Arquivo gerado por `lnc readmes`. Para registrar decisões de tradução deste volume use "
        "`NOTAS_DE_ADAPTACAO.md`; para acompanhar a revisão use `REVISAO.md`.",
        "",
        "| | |",
        "|---|---|",
        f"| Obra | {serie.get('titulo', vol.slug)} |",
        f"| Autor | {serie.get('autor', '?')} |",
        f"| Título no EPUB de origem | {vol.manifest.get('title') or '-'} |",
        f"| Traduzido do | {idioma} |",
        f"| EPUB de origem | {('`' + rel_repo(origem) + '`') if origem else '_não está no repositório_'} |",
        f"| EPUB publicado | {('`' + rel_repo(vol.publicado_path) + '`') if vol.publicado_path.exists() else '_ainda não publicado_'} |",
        f"| Arquivos de texto | {len(vol.entradas)} |",
        "",
        "## O que tem aqui",
        "",
        "| Caminho | Para que serve | Pode editar? |",
        "|---|---|---|",
        "| `translation/translated-html/` | **A tradução.** Um arquivo por seção do livro, na ordem de leitura. | ✅ Sim, é aqui que se traduz e revisa |",
        "| `translation/original-html/` | O texto original, para comparar. | ❌ Não |",
        "| `translation/plaintext/` | O original em texto puro (apoio para IA/consulta). | ❌ Não |",
        "| `package/` | O EPUB descompactado. É atualizado pelo `lnc sincronizar`. | ❌ Não edite à mão |",
        "| `manifest.json` | Mapa que liga cada arquivo de tradução ao arquivo do EPUB. | ❌ Não |",
    ]
    if notas.exists():
        linhas.append("| `NOTAS_DE_ADAPTACAO.md` | Termos, vozes de personagem e etapas decididos para este volume. | ✅ Sim |")
    linhas += [
        "| `REVISAO.md` | Checklist de revisão por arquivo. | ✅ Sim |",
        "",
        "## Comandos",
        "",
        "```bash",
        f"python ferramentas/lnc.py comparar {vol.id} --abrir   # original x tradução lado a lado",
        f"python ferramentas/lnc.py validar  {vol.id}           # checagens automáticas",
        f"python ferramentas/lnc.py publicar {vol.id}           # sincroniza, monta e copia para publicados/",
        "```",
        "",
    ]
    escrever_texto(vol.dir / "README.md", "\n".join(linhas))

    rev = vol.dir / "REVISAO.md"
    if criar_revisao and not rev.exists():
        itens = "\n".join(f"- [ ] `{n}` · revisor: · obs:" for n in _lista_arquivos_com_texto(vol))
        escrever_texto(rev, "\n".join([
            f"# Revisão: {vol.titulo}",
            "",
            "Marque `[x]` quando terminar de revisar um arquivo e coloque seu nome. "
            "Problemas que você não conseguiu resolver vão em `obs:` (ou numa issue no GitHub).",
            "Passo a passo em [`docs/GUIA_DO_REVISOR.md`](../../../docs/GUIA_DO_REVISOR.md).",
            "",
            "Páginas só com imagem não aparecem na lista.",
            "",
            itens,
            "",
        ]))


def gerar_readme_serie(slug: str) -> None:
    serie = carregar_serie(slug)
    path = OBRAS / slug / "README.md"
    idioma = {"ja": "japonês", "en": "inglês"}.get(serie.get("idiomaOrigem", ""), serie.get("idiomaOrigem", "?"))
    linhas = [
        f"# {serie.get('titulo', slug)}",
        "",
        f"- **Autor:** {serie.get('autor', '?')}",
        f"- **Traduzido do:** {idioma}",
    ]
    if serie.get("fonte"):
        linhas.append(f"- **Fonte usada:** {serie['fonte']}")
    if serie.get("observacoes"):
        linhas += ["", serie["observacoes"]]
    linhas += ["", "## Volumes", "", STATUS_INICIO, "", STATUS_FIM, "",
               "Os dados da obra (título, autor, idioma) ficam em `serie.json`.", ""]
    escrever_texto(path, "\n".join(linhas))
    vols = [v for v in todos_volumes() if v.slug == slug]
    substituir_bloco_status(path, tabela_status(vols, com_obra=False), OBRAS / slug)


def cmd_readmes(args) -> None:
    vols = volumes_do_arg(args.volume)
    for vol in vols:
        gerar_readmes(vol, criar_revisao=True)
        print(f"{vol.id}: README.md gerado" + ("" if not (vol.dir / "REVISAO.md").exists() else " (REVISAO.md ok)"))
    for slug in sorted({v.slug for v in vols}):
        gerar_readme_serie(slug)
        print(f"obras/{slug}/README.md gerado")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(prog="lnc", description="Ferramenta do LightNovelsCafe.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="comando")

    s = sub.add_parser("status", help="situação de todas as obras/volumes")
    s.add_argument("--obra", help="filtra por obra (slug)")
    s.add_argument("--markdown", action="store_true", help="imprime em tabela Markdown")
    s.add_argument("--atualizar-readme", action="store_true", help="atualiza as tabelas de status dos README.md")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("comparar", help="gera HTML com original x tradução lado a lado")
    s.add_argument("volume")
    s.add_argument("--arquivo", help="só alguns arquivos, pelo número: 5 ou 5-8 ou 3,7")
    s.add_argument("--abrir", action="store_true", help="abre no navegador")
    s.set_defaults(func=cmd_comparar)

    s = sub.add_parser("validar", help="checagens automáticas da tradução")
    s.add_argument("volume", help="volume, obra ou 'todos'")
    s.add_argument("--tudo", action="store_true", help="mostra todos os achados")
    s.set_defaults(func=cmd_validar)

    s = sub.add_parser("preparar", help="cria o workbench de um volume novo a partir de um EPUB")
    s.add_argument("epub")
    s.add_argument("obra", help="slug da obra, ex.: lord-el-melloi-ii-case-files")
    s.add_argument("numero", help="número do volume")
    s.add_argument("--titulo", help="título da obra (só se a obra for nova)")
    s.add_argument("--autor", help="autor (só se a obra for nova)")
    s.add_argument("--forcar", action="store_true", help="recria o volume se já existir (APAGA o trabalho)")
    s.set_defaults(func=cmd_preparar)

    s = sub.add_parser("sincronizar", help="copia translated-html para package/")
    s.add_argument("volume")
    s.set_defaults(func=cmd_sincronizar)

    s = sub.add_parser("montar", help="gera o EPUB em build/ a partir de package/")
    s.add_argument("volume")
    s.set_defaults(func=cmd_montar)

    s = sub.add_parser("publicar", help="validar + sincronizar + montar + copiar para publicados/")
    s.add_argument("volume")
    s.add_argument("--forcar", action="store_true", help="publica mesmo com erros de validação")
    s.set_defaults(func=cmd_publicar)

    s = sub.add_parser("readmes", help="(re)gera README.md de volumes e obras; cria REVISAO.md")
    s.add_argument("volume", nargs="?", default="todos")
    s.set_defaults(func=cmd_readmes)

    s = sub.add_parser("restaurar-originais", help="recria original-html/plaintext faltantes a partir de um EPUB")
    s.add_argument("volume")
    s.add_argument("epub")
    s.add_argument("--forcar", action="store_true", help="sobrescreve os que já existem")
    s.set_defaults(func=cmd_restaurar_originais)

    s = sub.add_parser("converter-manifest", help="converte manifest antigo para caminhos relativos ao volume")
    s.add_argument("volume")
    s.set_defaults(func=cmd_converter_manifest)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
