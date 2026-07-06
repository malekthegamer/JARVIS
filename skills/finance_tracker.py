"""Finance & data — stock/crypto prices (yfinance), news headlines (RSS), and a
simple local budget. Price-target alerts surface as dashboard notifications only."""
from __future__ import annotations

import json

import config
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


def _load_budget() -> dict:
    if config.BUDGET_FILE.exists():
        try:
            return json.loads(config.BUDGET_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"entries": []}
    return {"entries": []}


@register_skill
class FinanceTrackerSkill(Skill):
    name = "finance_tracker"
    description = "Look up stock/crypto prices, read news headlines, and track a simple local budget."

    def tools(self) -> list[dict]:
        return [
            tool("get_price", "Get the current price of a stock or crypto ticker (e.g. AAPL, BTC-USD).",
                 {"ticker": prop("string", "Ticker symbol")}, ["ticker"]),
            tool("news_headlines", "Get recent news headlines from an RSS feed.",
                 {"topic": prop("string", "Optional: a feed URL, or leave blank for top tech news")}),
            tool("log_expense", "Record an expense in the local budget.",
                 {"amount": prop("number", "Amount spent"),
                  "category": prop("string", "Category, e.g. food, transport")}, ["amount", "category"]),
            tool("budget_summary", "Summarize spending recorded in the local budget."),
        ]

    def execute(self, tool: str, args: dict) -> str:
        try:
            return getattr(self, f"_{tool}")(args)
        except Exception as exc:
            self.log(tool, args, "error")
            return f"Finance lookup failed: {exc}"

    def _get_price(self, args) -> str:
        import yfinance as yf
        ticker = str(args.get("ticker", "")).upper()
        data = yf.Ticker(ticker).history(period="1d")
        if data.empty:
            return f"No data for {ticker}."
        price = data["Close"].iloc[-1]
        self.log("get_price", {"ticker": ticker})
        return f"{ticker} is at ${price:,.2f}."

    def _news_headlines(self, args) -> str:
        import feedparser
        topic = str(args.get("topic", "")).strip()
        url = topic if topic.startswith("http") else "https://feeds.arstechnica.com/arstechnica/index"
        feed = feedparser.parse(url)
        heads = [f"• {e.title}" for e in feed.entries[:6]]
        self.log("news_headlines", {"url": url})
        return "Headlines:\n" + "\n".join(heads) if heads else "No headlines found."

    def _log_expense(self, args) -> str:
        budget = _load_budget()
        budget["entries"].append({"amount": float(args.get("amount", 0)),
                                  "category": str(args.get("category", "misc"))})
        config.BUDGET_FILE.write_text(json.dumps(budget, indent=2), encoding="utf-8")
        self.log("log_expense", {"category": args.get("category")})
        return f"Logged ${float(args.get('amount', 0)):.2f} for {args.get('category')}."

    def _budget_summary(self, args) -> str:
        budget = _load_budget()
        if not budget["entries"]:
            return "No expenses logged yet."
        totals: dict[str, float] = {}
        for e in budget["entries"]:
            totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
        total = sum(totals.values())
        self.log("budget_summary")
        lines = "\n".join(f"  {c}: ${v:.2f}" for c, v in sorted(totals.items(), key=lambda x: -x[1]))
        return f"Total spent: ${total:.2f}\n{lines}"
