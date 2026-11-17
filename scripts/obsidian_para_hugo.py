#!/usr/bin/env python3
"""Converte notas no formato do Obsidian do Gustavo para posts do Hugo.

Formato aceito (o mesmo dos templates do vault):

    06-07-2026 22:17

    Tags: #escrita #estudos #aprendizado

    # Título do post

    Texto...

Também aceita "Tags: [[tag-a]] [[tag-b]]" e notas que já tenham front matter
YAML (os campos que já existirem são mantidos).

O script gera o front matter do Hugo (title, date, tags, slug) e tira do texto
as linhas de data, de Tags e o "# Título", que o tema já mostra. Wikilinks
viram texto simples ([[Nota|apelido]] -> apelido) e ![[imagem.png]] vira
imagem Markdown.

Uso:
    python3 scripts/obsidian_para_hugo.py content/blog          # converte no lugar
    python3 scripts/obsidian_para_hugo.py nota.md --dry-run     # só mostra o resultado

Arquivos que já estão no formato do Hugo (front matter com title) e sem
linha "Tags:" no começo ficam como estão, então dá pra rodar quantas vezes
quiser. No deploy, o GitHub Actions roda este script antes do build.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

RE_DATA = re.compile(r"^(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{1,2}):(\d{2}))?\s*$")
RE_TAGS = re.compile(r"^tags\s*:(.*)$", re.IGNORECASE)
RE_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$")
RE_TAG_HASH = re.compile(r"#([\w\-/áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]+)")
RE_TAG_LINK = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")
RE_EMBED = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
RE_WIKILINK = re.compile(r"\[\[([^\]|]+?)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()
    return texto or "post"


def separar_front_matter(texto: str) -> tuple[dict[str, str], list[str], str]:
    """Devolve (campos simples, linhas originais do YAML, corpo)."""
    if not texto.startswith("---\n"):
        return {}, [], texto
    fim = texto.find("\n---", 4)
    if fim == -1:
        return {}, [], texto
    bloco = texto[4:fim]
    corpo = texto[fim + 4 :].lstrip("\n")
    campos = {}
    for linha in bloco.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", linha)
        if m:
            campos[m.group(1).lower()] = m.group(2).strip()
    return campos, bloco.splitlines(), corpo


def extrair_tags(valor: str) -> list[str]:
    tags = RE_TAG_HASH.findall(valor) + RE_TAG_LINK.findall(valor)
    vistas, saida = set(), []
    for t in tags:
        t = t.strip().lower()
        if t and t not in vistas:
            vistas.add(t)
            saida.append(t)
    return saida


def yaml_str(valor: str) -> str:
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'


def converter(texto: str, nome_arquivo: str) -> str | None:
    """Retorna o texto convertido, ou None se não há nada a fazer."""
    campos, linhas_yaml, corpo = separar_front_matter(texto)
    linhas = corpo.splitlines()

    data = titulo = None
    tags: list[str] = []
    achou_algo = False

    # Lê o cabeçalho do Obsidian: data, Tags e o primeiro "# Título".
    i = 0
    while i < len(linhas):
        linha = linhas[i].strip()
        if not linha:
            i += 1
            continue
        if data is None and (m := RE_DATA.match(linha)):
            dd, mm, aaaa, hh, mi = m.groups()
            data = f"{aaaa}-{mm}-{dd}T{int(hh or 0):02d}:{mi or '00'}:00-03:00"
            linhas[i] = None
        elif not tags and (m := RE_TAGS.match(linha)):
            tags = extrair_tags(m.group(1))
            linhas[i] = None
            achou_algo = True
        elif titulo is None and (m := RE_H1.match(linha)):
            titulo = m.group(1).strip()
            linhas[i] = None
            achou_algo = True
            break
        else:
            break
        i += 1

    if data:
        achou_algo = True
    if not achou_algo and "title" in campos:
        return None  # já está no formato do Hugo

    corpo = "\n".join(l for l in linhas if l is not None).lstrip("\n")
    corpo = RE_EMBED.sub(lambda m: f"![{Path(m.group(1)).stem}]({m.group(1).strip()})", corpo)
    corpo = RE_WIKILINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), corpo)

    # Monta o front matter: mantém o que já existe e completa o que falta.
    novos: list[str] = []
    if "title" not in campos:
        novos.append(f"title: {yaml_str(titulo or Path(nome_arquivo).stem)}")
    if "date" not in campos and data:
        novos.append(f"date: {data}")
    if "slug" not in campos:
        novos.append(f"slug: {slugify(titulo or campos.get('title', '').strip(chr(34)) or Path(nome_arquivo).stem)}")
    if tags and "tags" not in campos:
        novos.append("tags:")
        novos.extend(f"  - {yaml_str(t)}" for t in tags)
    publicar = campos.get("publish", "").strip("\"'").lower()
    if "draft" not in campos:
        novos.append(f"draft: {'true' if publicar == 'false' else 'false'}")

    front = "\n".join([*linhas_yaml, *novos])
    return f"---\n{front}\n---\n\n{corpo.rstrip()}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caminhos", nargs="+", help="arquivos .md ou pastas (ex.: content/blog)")
    ap.add_argument("--dry-run", action="store_true", help="mostra o resultado sem salvar")
    args = ap.parse_args()

    arquivos: list[Path] = []
    for c in map(Path, args.caminhos):
        arquivos += sorted(c.rglob("*.md")) if c.is_dir() else [c]

    for arq in arquivos:
        if arq.name == "_index.md":
            continue
        novo = converter(arq.read_text(encoding="utf-8"), arq.name)
        if novo is None:
            continue
        if args.dry_run:
            print(f"===== {arq}\n{novo}")
        else:
            arq.write_text(novo, encoding="utf-8")
            print(f"convertido: {arq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
