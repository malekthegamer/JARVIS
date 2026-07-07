"""Research & knowledge — web search (DuckDuckGo, free), page fetch/summarize,
and PDF text extraction. Leaves a seam for a paid search API later."""
from __future__ import annotations

from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class WebResearchSkill(Skill):
    name = "web_research"
    description = "Search the web, fetch and summarize pages, and read PDFs to answer questions with current information."

    def tools(self) -> list[dict]:
        return [
            tool("web_search", "Search the web and get the top results (title, snippet, link).",
                 {"query": prop("string", "Search query"),
                  "count": prop("integer", "Number of results (default 5)")}, ["query"]),
            tool("fetch_page", "Fetch a web page and return its readable text for summarizing.",
                 {"url": prop("string", "Page URL")}, ["url"]),
            tool("read_pdf", "Extract text from a local PDF file.",
                 {"path": prop("string", "Full path to the .pdf")}, ["path"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:
            self.log(tool, args, "error")
            return f"Research failed: {exc}"

    def _web_search(self, args) -> str:
        query = str(args.get("query", ""))
        count = int(args.get("count", 5))
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # older package name
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=count):
                results.append(f"• {r.get('title','')}\n  {r.get('body','')[:200]}\n  {r.get('href','')}")
        self.log("web_search", {"query": query, "results": len(results)})
        return f"Results for '{query}':\n" + "\n".join(results) if results else f"No results for '{query}'."

    def _fetch_page(self, args) -> str:
        import requests
        from bs4 import BeautifulSoup
        url = str(args.get("url", ""))
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 JARVIS"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        self.log("fetch_page", {"url": url})
        return f"Text from {url} (first 5000 chars):\n{text[:5000]}"

    def _read_pdf(self, args) -> str:
        from pypdf import PdfReader
        path = str(args.get("path", ""))
        reader = PdfReader(path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:30])
        self.log("read_pdf", {"path": path, "pages": len(reader.pages)})
        return f"PDF text ({len(reader.pages)} pages, first 5000 chars):\n{text[:5000]}"
