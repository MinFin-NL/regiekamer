import json
import sqlite3

import httpx
import pytest

import db
import org
import templates
import worktools
from conftest import hire
from heartbeat import Scheduler, WakeRequest
from tools import Toolbox

SRU_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://docs.oasis-open.org/ns/search-ws/sruResponse">
  <numberOfRecords>2</numberOfRecords>
  <records>
    <record><recordData><gzd xmlns="http://standaarden.overheid.nl/sru" xmlns:dcterms="http://purl.org/dc/terms/"
        xmlns:overheidbwb="http://standaarden.overheid.nl/bwb/terms/">
      <originalData><overheidbwb:meta><owmskern>
        <dcterms:identifier>BWBR0047641</dcterms:identifier>
        <dcterms:title>Regeling indexering bedragen Algemene wet bestuursrecht</dcterms:title>
        <dcterms:type>ministeriele-regeling</dcterms:type>
      </owmskern></overheidbwb:meta></originalData>
      <enrichedData><overheidbwb:locatie_toestand>https://repo.test/regeling.xml</overheidbwb:locatie_toestand></enrichedData>
    </gzd></recordData></record>
    <record><recordData><gzd xmlns="http://standaarden.overheid.nl/sru" xmlns:dcterms="http://purl.org/dc/terms/"
        xmlns:overheidbwb="http://standaarden.overheid.nl/bwb/terms/">
      <originalData><overheidbwb:meta><owmskern>
        <dcterms:identifier>BWBR0005537</dcterms:identifier>
        <dcterms:title>Algemene wet bestuursrecht</dcterms:title>
        <dcterms:type>wet</dcterms:type>
      </owmskern></overheidbwb:meta></originalData>
      <enrichedData><overheidbwb:locatie_toestand>https://repo.test/awb.xml</overheidbwb:locatie_toestand></enrichedData>
    </gzd></recordData></record>
  </records>
</searchRetrieveResponse>"""

LAW_XML = """<toestand bwb-id="BWBR0005537"><wetgeving><wet-besluit><wettekst>
  <artikel label="Artikel 1:1" inwerking="2020-01-01">
    <kop><label>Artikel</label><nr>1:1</nr></kop>
    <lid><lidnr>1</lidnr><al>Onder bestuursorgaan wordt verstaan: </al>
      <lijst><li><li.nr>a.</li.nr><al>een orgaan van een rechtspersoon, of</al></li>
             <li><li.nr>b.</li.nr><al>een ander persoon met openbaar gezag.</al>
               <meta-data><jcis><jci verwijzing="x"/></jcis></meta-data></li></lijst></lid>
    <lid><lidnr>2</lidnr><al>Niet als bestuursorgaan geldt de wetgevende macht.</al></lid>
  </artikel>
  <artikel label="Artikel 1:3"><kop><nr>1:3</nr></kop><lid><lidnr>1</lidnr><al>Onder besluit wordt verstaan: ...</al></lid></artikel>
</wettekst></wet-besluit></wetgeving></toestand>"""

DDG_HTML = """<div class="result">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rijksoverheid.nl%2Fregeldruk&amp;rut=abc">Regeldruk | <b>Rijksoverheid</b></a>
<a class="result__snippet" href="#">Door <b>regeldruk</b> houden ondernemers minder tijd over.</a>
</div>
<div class="result">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad%3D1">Advertentie</a>
<a class="result__snippet" href="#">Koop nu</a>
</div>"""

PAGE_HTML = """<html><head><title> Regeldruk | Rijksoverheid.nl </title><style>body{}</style></head>
<body><nav>Menu Home Contact</nav><h1>Regeldruk</h1><p>Het kabinet   vermindert
regeldruk.</p><script>track()</script><p>Tweede alinea.</p></body></html>"""

CKAN_SEARCH = {"success": True, "result": {"count": 1, "results": [{
    "name": "regeldruk-monitor", "title": "Regeldrukmonitor", "notes": "<p>Cijfers over <b>regeldruk</b>.</p>",
    "organization": {"title": "Ministerie van EZ"}, "modified": "2026-01-01",
    "resources": [{"name": "csv", "format": "http://publications.europa.eu/resource/authority/file-type/CSV",
                   "url": "https://example.nl/data.csv"}],
}]}}


@pytest.fixture
def web(monkeypatch):
    """Route all work-tool HTTP through a fake; record what was asked."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if "zoekservice.overheid.nl" in url:
            if request.headers.get("accept") != "application/xml":
                return httpx.Response(406, text=SRU_RESPONSE)
            return httpx.Response(200, text=SRU_RESPONSE)
        if url == "https://repo.test/awb.xml":
            return httpx.Response(200, text=LAW_XML)
        if "duckduckgo.com" in url:
            return httpx.Response(200, text=DDG_HTML)
        if "package_search" in url:
            return httpx.Response(200, json=CKAN_SEARCH)
        if "package_show" in url:
            return httpx.Response(200, json={"success": True, "result": CKAN_SEARCH["result"]["results"][0]})
        if url == "https://www.rijksoverheid.nl/oud":
            return httpx.Response(301, headers={"location": "/regeldruk"})
        if url == "https://www.rijksoverheid.nl/regeldruk":
            return httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html; charset=utf-8"})
        if url.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def dns(host: str) -> list[str]:
        return ["192.168.1.20"] if host.endswith(".lan") else ["145.21.170.1"]

    monkeypatch.setattr(worktools, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(worktools, "_addresses", dns)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    worktools._law_cache.clear()
    return requests


async def researcher(company, ceo, toolsets=None) -> dict:
    extra = {"toolsets": toolsets} if toolsets is not None else {}
    agent = await hire(company["id"], template="researcher", name="Olaf", reports_to=ceo["id"], **extra)
    return db.get("agents", agent["id"])


def tool_names(agent: dict) -> set[str]:
    return {s["name"] for s in Toolbox(agent, "run-x").specs}


async def test_hire_takes_template_toolsets_unless_overridden(company, ceo):
    default = await researcher(company, ceo)
    assert org.agent_view(default)["toolsets"] == ["documenten", "web", "opendata"]
    assert {"web_search", "search_datasets", "write_document"} <= tool_names(default)
    assert "search_law" not in tool_names(default)

    none = await hire(company["id"], template="jurist", name="Jet", reports_to=ceo["id"], toolsets=[])
    assert org.agent_view(none)["toolsets"] == []
    assert tool_names(none) == {s["name"] for s in Toolbox(ceo, "r").specs} - set(worktools.TOOLSET_OF)

    with pytest.raises(ValueError, match="Onbekende toolset"):
        org.hire_agent(company["id"], {"template": "jurist", "name": "X", "toolsets": ["telepathie"]})


async def test_toolsets_show_up_in_prompt_and_can_be_changed(company, ceo):
    agent = await researcher(company, ceo, ["wetten"])
    assert "search_law" in templates.agent_prompt(agent)
    assert "web_search" not in templates.agent_prompt(agent)

    updated = await org.update_agent(agent["id"], {"toolsets": ["web", "wetten", "web"]})
    assert templates.agent_toolsets(updated) == ["wetten", "web"]  # catalog order, no duplicates
    assert "web_search" in tool_names(updated)


async def test_agents_from_before_toolsets_get_template_defaults(company, ceo, tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, company_id TEXT, name TEXT, template TEXT)")
    old.execute("INSERT INTO agents VALUES ('a1', 'c1', 'Jet', 'jurist')")
    old.commit()
    old.close()

    db.connect(str(path))
    agent = db.get("agents", "a1")
    assert agent["toolsets"] is None
    assert templates.agent_toolsets(agent) == ["documenten", "wetten"]


async def test_tool_outside_own_toolsets_is_refused(company, ceo):
    agent = await researcher(company, ceo, ["documenten"])
    result = await Toolbox(agent, "run-1").call("search_law", {"query": "awb"})
    assert "hoort niet bij jouw werktools" in result["error"]


async def test_documents_are_versioned_by_title_and_linked_to_issue(company, ceo):
    agent = await researcher(company, ceo)
    issue = org.create_issue(company["id"], {"title": "Notitie regeldruk", "assignee_agent_id": agent["id"]})
    toolbox = Toolbox(agent, "run-1")

    first = await toolbox.call("write_document", {"title": "Regeldruk", "body": "v1", "issue": issue["identifier"]})
    second = await toolbox.call("write_document", {"name": "regeldruk", "content": "v2"})
    assert first["document"] == second["document"] and second["version"] == 2

    read = await toolbox.call("read_document", {"id": "Regeldruk"})
    assert read["body"] == "v2" and read["issue"] == issue["identifier"]
    listed = await toolbox.call("list_documents", {"q": "v2"})
    assert [d["title"] for d in listed["documents"]] == ["Regeldruk"]
    missing = await toolbox.call("read_document", {"document": "Bestaat niet"})
    assert "list_documents" in missing["error"]
    # Writing a document for the focus issue counts as working on it: no nudge.
    assert toolbox.unfinished_work(issue["identifier"]) is None


async def test_law_search_and_article_text(company, ceo, web):
    agent = await researcher(company, ceo, ["wetten"])
    toolbox = Toolbox(agent, "run-1")

    found = await toolbox.call("search_law", {"q": "bestuursrecht"})
    assert found["results"][0] == {"law": "BWBR0005537", "title": "Algemene wet bestuursrecht", "type": "wet"}
    assert "geldigheidsdatum" in str(web[0].url)

    article = await toolbox.call("get_law_article", {"law": "BWBR0005537", "artikel": "Artikel 1:1"})
    assert article["text"] == (
        "1. Onder bestuursorgaan wordt verstaan:\n"
        "   a. een orgaan van een rechtspersoon, of\n"
        "   b. een ander persoon met openbaar gezag.\n"
        "2. Niet als bestuursorgaan geldt de wetgevende macht."
    )
    assert article["source"] == "https://wetten.overheid.nl/jci1.3:c:BWBR0005537&artikel=1:1"

    by_title = await toolbox.call("get_law_article", {"law": "Algemene wet bestuursrecht", "article": "1:3"})
    assert by_title["law"] == "BWBR0005537"
    assert sum("awb.xml" in str(r.url) for r in web) == 1  # law XML is cached

    wrong = await toolbox.call("get_law_article", {"law": "BWBR0005537", "article": "1:99"})
    assert "1:1, 1:3" in wrong["error"]


async def test_web_search_and_fetch(company, ceo, web):
    toolbox = Toolbox(await researcher(company, ceo, ["web"]), "run-1")

    results = (await toolbox.call("web_search", {"query": "regeldruk"}))["results"]
    assert results == [{"title": "Regeldruk | Rijksoverheid", "url": "https://www.rijksoverheid.nl/regeldruk",
                        "snippet": "Door regeldruk houden ondernemers minder tijd over."}]

    page = await toolbox.call("fetch_url", {"url": "https://www.rijksoverheid.nl/oud"})
    assert page == {"url": "https://www.rijksoverheid.nl/regeldruk", "title": "Regeldruk | Rijksoverheid.nl",
                    "text": "Regeldruk\nHet kabinet vermindert regeldruk.\nTweede alinea."}

    assert "geen webpagina" in (await toolbox.call("fetch_url", {"url": "https://x.nl/a.pdf"}))["error"]
    assert "publiek" in (await toolbox.call("fetch_url", {"url": "https://intern.lan/"}))["error"]
    assert "http(s)" in (await toolbox.call("fetch_url", {"url": "file:///etc/passwd"}))["error"]


async def test_private_addresses_are_blocked():
    for url in ("http://localhost:8000/api/health", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.1/", "http://[::1]/"):
        with pytest.raises(worktools.WorkToolError, match="geen publiek"):
            await worktools._check_public(url)


async def test_open_data_search_and_details(company, ceo, web):
    toolbox = Toolbox(await researcher(company, ceo, ["opendata"]), "run-1")
    found = await toolbox.call("search_datasets", {"query": "regeldruk"})
    assert found["results"][0] == {
        "dataset": "regeldruk-monitor", "title": "Regeldrukmonitor", "publisher": "Ministerie van EZ",
        "modified": "2026-01-01", "formats": ["CSV"], "summary": "Cijfers over regeldruk."}
    assert web[0].headers["user-agent"].startswith("Regiekamer")  # data.overheid.nl drops requests without one

    details = await toolbox.call("get_dataset", {"name": "regeldruk-monitor"})
    assert details["resources"] == [{"name": "csv", "format": "CSV", "url": "https://example.nl/data.csv"}]
    assert details["source"] == "https://data.overheid.nl/dataset/regeldruk-monitor"


async def test_unreachable_source_comes_back_as_tool_error(company, ceo, monkeypatch):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("geen verbinding", request=request)

    monkeypatch.setattr(worktools, "_transport", httpx.MockTransport(down))
    toolbox = Toolbox(await researcher(company, ceo, ["opendata"]), "run-1")
    result = await toolbox.call("search_datasets", {"query": "x"})
    assert "niet bereikbaar" in result["error"]
    assert toolbox.calls[-1]["ok"] is False


async def test_mock_leaf_writes_its_work_as_a_document(company, ceo):
    agent = await researcher(company, ceo)
    issue = org.create_issue(company["id"], {"title": "Indicatoren", "assignee_agent_id": agent["id"]})
    await Scheduler().execute(WakeRequest(agent["id"], "assignment", issue_ids=[issue["id"]]))
    doc = db.one("SELECT * FROM documents WHERE issue_id = ?", issue["id"])
    assert doc and doc["author_agent_id"] == agent["id"]
    run = db.one("SELECT usage_json FROM heartbeat_runs WHERE agent_id = ?", agent["id"])
    assert "write_document" in [c["tool"] for c in json.loads(run["usage_json"])["tool_calls"]]
