"""Work tools: the toolsets switched on per agent.

tools.py holds the coordination tools every agent has (claim, comment,
delegate, ...). This module holds the optional ones: shared documents, Dutch
law (wetten.overheid.nl), the web, and open data (data.overheid.nl). Each
template has defaults; the board can change them in the hire form and on the
agent page.

Every tool takes the calling Toolbox as its first argument, so it runs scoped
to one agent and one heartbeat run, like the coordination tools. Mistakes and
unreachable sources come back as WorkToolError, which the Toolbox turns into
an {"error": ...} result the model can react to.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import socket
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

import db
import events

if TYPE_CHECKING:
    from tools import Toolbox

USER_AGENT = "Regiekamer/0.1 (AI-agentdemo; +https://wetten.overheid.nl)"
HTTP_TIMEOUT = float(os.environ.get("WORKTOOL_HTTP_TIMEOUT_SEC", "20"))
# Tool output goes into the model's context; local models have 16k tokens in total.
MAX_PAGE_CHARS = 6000
MAX_ARTICLE_CHARS = 4000
MAX_FETCH_BYTES = 3_000_000
SRU_URL = "https://zoekservice.overheid.nl/sru/Search"
CKAN_URL = "https://data.overheid.nl/data/api/3/action"

# Tests swap in an httpx.MockTransport so nothing leaves the machine.
_transport: httpx.AsyncBaseTransport | None = None


class WorkToolError(Exception):
    pass


def _client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=HTTP_TIMEOUT,
                             headers={"User-Agent": USER_AGENT}, **kwargs)


def _spec(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or [],
                       "additionalProperties": False},
    }


_STR = {"type": "string"}

TOOLSETS: list[dict] = [
    {
        "key": "documenten",
        "label": "Documenten",
        "description": "Nota's, analyses en concepten vastleggen als gedeelde documenten die collega's en het bestuur kunnen lezen.",
        "online": False,
        "hint": "Leg uitgebreide uitwerkingen (nota's, analyses, concepten) vast met write_document en verwijs er in "
                "je opmerking naar. Kijk met list_documents en read_document eerst of er al iets bestaat.",
        "tools": [
            _spec("write_document",
                  "Maak een gedeeld document of werk het bij (zelfde titel = nieuwe versie). Koppel het aan een issue.",
                  {"title": _STR, "body": {"type": "string", "description": "Volledige tekst (Markdown)"},
                   "issue": {"type": "string", "description": "Issue waar het document bij hoort, bijv. RK-3"}},
                  ["title", "body"]),
            _spec("read_document", "Lees een gedeeld document helemaal.",
                  {"document": {"type": "string", "description": "Document-id of titel"}}, ["document"]),
            _spec("list_documents", "Zoek in de gedeelde documenten van de organisatie (leeg = de nieuwste).",
                  {"query": {"type": "string", "description": "Zoekwoord in titel of tekst"}}),
        ],
    },
    {
        "key": "wetten",
        "label": "Wetten opzoeken",
        "description": "Geldende Nederlandse wet- en regelgeving zoeken en artikelen letterlijk lezen via wetten.overheid.nl.",
        "online": True,
        "hint": "Citeer wetsartikelen alleen nadat je ze hebt opgezocht met search_law en get_law_article, en noem "
                "de bron-URL. EU-verordeningen (zoals de AVG zelf) staan daar niet in, de uitvoeringswetten wel.",
        "tools": [
            _spec("search_law", "Zoek geldende wetten en regelingen op titel in wetten.overheid.nl.",
                  {"query": {"type": "string", "description": "Woorden uit de titel, bijv. 'bestuursrecht' of 'Wet open overheid'"}},
                  ["query"]),
            _spec("get_law_article", "Lees de geldende tekst van één artikel.",
                  {"law": {"type": "string", "description": "BWB-id (bijv. BWBR0005537) of titel van de wet"},
                   "article": {"type": "string", "description": "Artikelnummer, bijv. '1:3' of '5a'"}},
                  ["law", "article"]),
        ],
    },
    {
        "key": "web",
        "label": "Web zoeken",
        "description": "Op internet zoeken en webpagina's lezen, voor deskresearch met echte bronnen.",
        "online": True,
        "hint": "Zoek feiten op met web_search en lees de bron met fetch_url voordat je hem gebruikt. Noem altijd de "
                "URL's waarop je conclusies rusten.",
        "tools": [
            _spec("web_search", "Zoek op internet. Geeft titels, links en korte fragmenten.",
                  {"query": _STR}, ["query"]),
            _spec("fetch_url", "Lees de tekst van een webpagina.",
                  {"url": {"type": "string", "description": "Volledige http(s)-URL"}}, ["url"]),
        ],
    },
    {
        "key": "opendata",
        "label": "Open data",
        "description": "Datasets van de overheid vinden en beoordelen via data.overheid.nl.",
        "online": True,
        "hint": "Zoek databronnen met search_datasets en bekijk ze met get_dataset. Noem de dataset-links en verzin "
                "geen cijfers die niet in een bron staan.",
        "tools": [
            _spec("search_datasets", "Zoek datasets op data.overheid.nl.", {"query": _STR}, ["query"]),
            _spec("get_dataset", "Details van één dataset: beschrijving, eigenaar, licentie en downloads.",
                  {"dataset": {"type": "string", "description": "Dataset-naam of id uit search_datasets"}},
                  ["dataset"]),
        ],
    },
]

_BY_KEY = {t["key"]: t for t in TOOLSETS}
TOOLSET_KEYS = [t["key"] for t in TOOLSETS]
ALL_SPECS: list[dict] = [spec for t in TOOLSETS for spec in t["tools"]]
TOOLSET_OF = {spec["name"]: t["key"] for t in TOOLSETS for spec in t["tools"]}


def validate(keys: list[str]) -> list[str]:
    unknown = [k for k in keys if k not in _BY_KEY]
    if unknown:
        raise ValueError(f"Onbekende toolset(s): {', '.join(unknown)}. Kies uit: {', '.join(TOOLSET_KEYS)}")
    return [k for k in TOOLSET_KEYS if k in keys]  # stable order, no duplicates


def specs_for(keys: list[str]) -> list[dict]:
    return [spec for k in keys if k in _BY_KEY for spec in _BY_KEY[k]["tools"]]


def prompt_section(keys: list[str]) -> str:
    sets = [_BY_KEY[k] for k in keys if k in _BY_KEY]
    if not sets:
        return ""
    return "Je werktools:\n" + "\n".join(f"- {t['label']}: {t['hint']}" for t in sets)


def catalog() -> list[dict]:
    """The toolsets as the UI shows them."""
    return [{k: t[k] for k in ("key", "label", "description", "online")}
            | {"tools": [{"name": s["name"], "description": s["description"]} for s in t["tools"]]}
            for t in TOOLSETS]


async def call(toolbox: Toolbox, name: str, args: dict) -> dict:
    fn = globals()[f"_w_{name}"]
    try:
        return await fn(toolbox, **args)
    except httpx.TimeoutException as exc:
        raise WorkToolError(f"De externe bron reageerde niet binnen {HTTP_TIMEOUT:.0f} s. Probeer het later.") from exc
    except httpx.HTTPError as exc:
        raise WorkToolError(f"Externe bron niet bereikbaar: {exc}") from exc


# ── Documenten ──────────────────────────────────────────────────────────────


def document_view(doc: dict) -> dict:
    issue = db.get("issues", doc["issue_id"]) if doc["issue_id"] else None
    author = db.get("agents", doc["author_agent_id"]) if doc["author_agent_id"] else None
    return {**doc,
            "issue": {"id": issue["id"], "identifier": issue["identifier"], "title": issue["title"]} if issue else None,
            "author": {"id": author["id"], "name": author["name"], "icon": author["icon"]} if author else None}


def _find_document(company_id: str, ref: str) -> dict | None:
    ref = (ref or "").strip()
    return db.one("SELECT * FROM documents WHERE company_id = ? AND (id = ? OR LOWER(title) = LOWER(?))"
                  " ORDER BY updated_at DESC", company_id, ref, ref)


async def _w_write_document(tb: Toolbox, title: str, body: str, issue: str | None = None) -> dict:
    title = title.strip()[:200]
    linked = tb._issue(issue) if issue else None
    existing = _find_document(tb.company_id, title)
    ts = db.now()
    if existing:
        db.update("documents", existing["id"], body=body, version=existing["version"] + 1, updated_at=ts,
                  author_agent_id=tb.agent["id"], issue_id=linked["id"] if linked else existing["issue_id"])
        doc = db.get("documents", existing["id"])
    else:
        doc = db.insert("documents", company_id=tb.company_id, title=title, body=body, version=1,
                        issue_id=linked["id"] if linked else None, author_agent_id=tb.agent["id"],
                        created_at=ts, updated_at=ts)
    verb = "maakte" if doc["version"] == 1 else f"werkte bij (versie {doc['version']})"
    events.publish(tb.company_id, "document.written", f"{tb.agent['name']} {verb} document '{title}'",
                   agent_id=tb.agent["id"], issue_id=doc["issue_id"])
    return {"ok": True, "document": doc["id"], "title": title, "version": doc["version"],
            "note": "Verwijs in je opmerking naar dit document; de tekst komt niet vanzelf in het issue."}


async def _w_read_document(tb: Toolbox, document: str) -> dict:
    doc = _find_document(tb.company_id, document)
    if not doc:
        raise WorkToolError(f"Document '{document}' bestaat niet. Gebruik list_documents om te zoeken.")
    view = document_view(doc)
    return {"document": doc["id"], "title": doc["title"], "version": doc["version"],
            "issue": view["issue"]["identifier"] if view["issue"] else None,
            "author": view["author"]["name"] if view["author"] else None, "body": doc["body"]}


async def _w_list_documents(tb: Toolbox, query: str | None = None) -> dict:
    sql, params = "SELECT * FROM documents WHERE company_id = ?", [tb.company_id]
    if query:
        sql += " AND (title LIKE ? OR body LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    docs = [document_view(d) for d in db.rows(sql + " ORDER BY updated_at DESC LIMIT 15", *params)]
    return {"documents": [
        {"document": d["id"], "title": d["title"], "version": d["version"],
         "issue": d["issue"]["identifier"] if d["issue"] else None,
         "author": d["author"]["name"] if d["author"] else None, "preview": d["body"][:200]}
        for d in docs
    ]}


# ── Wetten (wetten.overheid.nl via de SRU-zoekservice van KOOP) ─────────────

_BWB_ID = re.compile(r"^BWB[RV]\d{7}$", re.IGNORECASE)
# Consolidated law XML is large (the Awb is ~2.6 MB): keep a few in memory.
_law_cache: dict[str, ET.Element] = {}
_LAW_CACHE_SIZE = 8


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(el: ET.Element, name: str) -> str:
    for child in el.iter():
        if _local(child.tag) == name and (child.text or "").strip():
            return child.text.strip()
    return ""


async def _sru(query: str, limit: int) -> list[dict]:
    today = date.today().isoformat()
    params = {"operation": "searchRetrieve", "version": "2.0", "x-connection": "BWB",
              "maximumRecords": str(limit), "query": f"{query} and overheidbwb.geldigheidsdatum={today}"}
    async with _client() as http:
        # Without an explicit XML Accept header the service answers 406 (with a valid body).
        resp = await http.get(SRU_URL, params=params, headers={"Accept": "application/xml"})
        resp.raise_for_status()
    root = ET.fromstring(resp.content)
    found = []
    for record in (el for el in root.iter() if _local(el.tag) == "recordData"):
        found.append({"law": _first(record, "identifier"), "title": _first(record, "title"),
                      "type": _first(record, "type"), "xml": _first(record, "locatie_toestand")})
    return found


def _cql_words(text: str) -> str:
    return re.sub(r'["\\]', " ", text).strip()


async def _w_search_law(tb: Toolbox, query: str) -> dict:
    words = _cql_words(query)
    if not words:
        raise WorkToolError("Geef een of meer woorden uit de titel van de wet.")
    hits = await _sru(f'overheidbwb.titel all "{words}"', 8) or await _sru(f'overheidbwb.titel any "{words}"', 8)
    # Wetten first: a question about "de Awb" rarely means an indexeringsregeling.
    hits.sort(key=lambda h: h["type"] != "wet")
    return {"results": [{k: h[k] for k in ("law", "title", "type")} for h in hits],
            "note": "Lees een artikel met get_law_article(law=<BWB-id>, article=<nummer>)." if hits else
                    "Niets gevonden; probeer andere of minder woorden."}


async def _law_xml(law: str) -> tuple[str, str, ET.Element]:
    law = law.strip()
    if _BWB_ID.match(law):
        hits = [h for h in await _sru(f"dcterms.identifier=={law.upper()}", 1) if h["law"].upper() == law.upper()]
    else:
        words = _cql_words(law)
        hits = await _sru(f'overheidbwb.titel all "{words}"', 5)
        hits.sort(key=lambda h: (h["type"] != "wet", len(h["title"])))
    if not hits or not hits[0]["xml"]:
        raise WorkToolError(f"Geen geldende regeling gevonden voor '{law}'. Zoek eerst met search_law.")
    hit = hits[0]
    if hit["xml"] not in _law_cache:
        async with _client() as http:
            resp = await http.get(hit["xml"])
            resp.raise_for_status()
        if len(_law_cache) >= _LAW_CACHE_SIZE:
            _law_cache.pop(next(iter(_law_cache)))
        _law_cache[hit["xml"]] = ET.fromstring(resp.content)
    return hit["law"], hit["title"], _law_cache[hit["xml"]]


def _article_nr(value: str) -> str:
    return re.sub(r"^(artikel|art\.?)\s*", "", value.strip(), flags=re.IGNORECASE).strip().rstrip(".").lower()


def _article_text(article: ET.Element) -> str:
    """Leden and onderdelen as readable lines; metadata and footnotes dropped."""
    lines: list[str] = []

    def walk(el: ET.Element, prefix: str = "") -> None:
        tag = _local(el.tag)
        if tag in ("meta-data", "kop", "noot", "redactie"):
            return
        if tag in ("al", "tussenkop"):
            text = " ".join("".join(el.itertext()).split())
            if text:
                lines.append(prefix + text)
            return
        if tag == "lid":
            prefix = f"{(el.findtext('lidnr') or '').strip()}. "
        elif tag == "li":
            prefix = f"   {(el.findtext('li.nr') or '').strip()} "
        for child in el:
            before = len(lines)
            walk(child, prefix)
            if len(lines) > before:  # only the first line of a lid/onderdeel carries its number
                prefix = " " * len(prefix)

    walk(article)
    return "\n".join(lines)


async def _w_get_law_article(tb: Toolbox, law: str, article: str) -> dict:
    bwb_id, title, root = await _law_xml(law)
    wanted = _article_nr(article)
    articles = [a for a in root.iter() if _local(a.tag) == "artikel"]
    match = next((a for a in articles if (a.findtext("kop/nr") or "").strip().lower() == wanted), None)
    if match is None:
        nrs = [(a.findtext("kop/nr") or "").strip() for a in articles]
        close = [n for n in nrs if n and n.lower().startswith(wanted.split(":")[0])][:20]
        raise WorkToolError(f"Artikel {article} staat niet in {title} ({bwb_id}). "
                            f"Bestaande artikelen die erop lijken: {', '.join(close) or ', '.join(nrs[:20])}")
    text = _article_text(match)
    return {
        "law": bwb_id, "title": title, "article": (match.findtext("kop/nr") or "").strip(),
        "in_force_since": match.get("inwerking"),
        "text": text[:MAX_ARTICLE_CHARS] + (" …(ingekort)" if len(text) > MAX_ARTICLE_CHARS else ""),
        "source": f"https://wetten.overheid.nl/jci1.3:c:{bwb_id}&artikel={wanted}",
    }


# ── Web ─────────────────────────────────────────────────────────────────────


def _public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_global and not addr.is_multicast


async def _check_public(url: str) -> None:
    """No fetching of localhost, the LAN or the cloud metadata endpoint (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WorkToolError("Alleen volledige http(s)-URL's, bijv. https://www.rijksoverheid.nl/...")
    try:
        addresses = await _addresses(parsed.hostname)
    except socket.gaierror as exc:
        raise WorkToolError(f"Onbekende website: {parsed.hostname}") from exc
    if not addresses or not all(_public_ip(ip) for ip in addresses):
        raise WorkToolError(f"{parsed.hostname} is geen publiek internetadres")


async def _addresses(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            # Line breaks in HTML source are just whitespace; only block tags start a new line.
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = (" ".join(line.split()) for line in raw.splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(markup: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(markup)
    return " ".join(parser.title.split()), parser.text()


async def _w_fetch_url(tb: Toolbox, url: str) -> dict:
    url = url.strip()
    async with _client(follow_redirects=False) as http:
        for _ in range(4):
            await _check_public(url)
            async with http.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise WorkToolError(f"{url} gaf HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and not (ctype.startswith("text/") or ctype in ("application/json", "application/xhtml+xml",
                                                                        "application/xml")):
                    raise WorkToolError(f"{url} is geen webpagina maar {ctype}; dat kan fetch_url niet lezen")
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_FETCH_BYTES:
                        break
                text_raw = body.decode(resp.encoding or "utf-8", errors="replace")
            break
        else:
            raise WorkToolError(f"Te veel doorverwijzingen vanaf {url}")
    title, text = html_to_text(text_raw) if "html" in ctype or not ctype else ("", text_raw)
    return {"url": url, "title": title,
            "text": text[:MAX_PAGE_CHARS] + (" …(ingekort)" if len(text) > MAX_PAGE_CHARS else "")}


def _strip_tags(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", markup)).split())


def _ddg_results(markup: str) -> list[dict]:
    results = []
    links = re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', markup, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', markup, re.DOTALL)
    for i, m in enumerate(links):
        href = html.unescape(m.group(1))
        target = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in target:  # ads
            continue
        results.append({"title": _strip_tags(m.group(2)), "url": target,
                        "snippet": _strip_tags(snippets[i]) if i < len(snippets) else ""})
    return results


async def _w_web_search(tb: Toolbox, query: str) -> dict:
    query = query.strip()
    if not query:
        raise WorkToolError("Geef een zoekopdracht.")
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    async with _client() as http:
        if brave_key:
            resp = await http.get("https://api.search.brave.com/res/v1/web/search",
                                  params={"q": query, "count": 6, "search_lang": "nl"},
                                  headers={"X-Subscription-Token": brave_key, "Accept": "application/json"})
            resp.raise_for_status()
            results = [{"title": r.get("title", ""), "url": r.get("url", ""),
                        "snippet": re.sub(r"<[^>]+>", "", r.get("description", ""))}
                       for r in resp.json().get("web", {}).get("results", [])]
        else:
            # No key: DuckDuckGo's HTML endpoint. Fine for a demo, not for volume.
            resp = await http.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "nl-nl"})
            resp.raise_for_status()
            results = _ddg_results(resp.text)
    return {"results": results[:6],
            "note": "Lees een bron met fetch_url voordat je hem gebruikt." if results else
                    "Geen resultaten; probeer andere zoekwoorden."}


# ── Open data (CKAN-API van data.overheid.nl) ───────────────────────────────


def _format(value: str | None) -> str:
    return (value or "").rstrip("/").rsplit("/", 1)[-1].upper()


async def _ckan(action: str, **params: Any) -> dict:
    async with _client() as http:
        resp = await http.get(f"{CKAN_URL}/{action}", params=params)
    if resp.status_code == 404:
        raise WorkToolError("Niet gevonden op data.overheid.nl. Zoek eerst met search_datasets.")
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise WorkToolError(f"data.overheid.nl gaf een fout: {data.get('error')}")
    return data["result"]


async def _w_search_datasets(tb: Toolbox, query: str) -> dict:
    result = await _ckan("package_search", q=query, rows=6)
    return {"total": result["count"], "results": [
        {"dataset": p["name"], "title": p["title"],
         "publisher": (p.get("organization") or {}).get("title"),
         "modified": p.get("modified") or p.get("metadata_modified"),
         "formats": sorted({_format(r.get("format")) for r in p.get("resources", []) if r.get("format")}),
         "summary": _strip_tags(p.get("notes") or "")[:250]}
        for p in result["results"]
    ]}


async def _w_get_dataset(tb: Toolbox, dataset: str) -> dict:
    p = await _ckan("package_show", id=dataset.strip())
    return {
        "dataset": p["name"], "title": p["title"],
        "description": _strip_tags(p.get("notes") or "")[:1500],
        "publisher": (p.get("organization") or {}).get("title"),
        "license": p.get("license_title"), "modified": p.get("modified") or p.get("metadata_modified"),
        "resources": [{"name": r.get("name"), "format": _format(r.get("format")), "url": r.get("url")}
                      for r in p.get("resources", [])[:10]],
        "source": f"https://data.overheid.nl/dataset/{p['name']}",
    }
