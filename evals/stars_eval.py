"""Live eval for the star check: real API, organic repos never flagged, known fakes flagged."""
import time

from common import report

from rd import stars

ORGANIC = ["langchain-ai/langgraph", "josssphzhou/poki", "shanselman/sandbox-mcp", "jlowin/fastmcp",
           "kkkkhazix/agent-autoloop", "voyager-so/vork"]
FAKE = ["m-boutaleb/LZS", "LaqiraProtocol/LaqiraToken", "diamcircle/go-xdr", "abrorbekuz/django-db-only"]


def main():
    res = {}
    for n in ORGANIC + FAKE:
        res[n] = stars.check({"title": n, "stars_per_day": None})
        time.sleep(2.1)
    checks = [(f"organic ok: {n}", res[n] is not None and res[n]["level"] == "ok", str(res[n])) for n in ORGANIC]
    checks += [(f"fake flagged: {n}", res[n] is not None and res[n]["level"] == "suspicious", str(res[n])) for n in FAKE]
    raise SystemExit(0 if report("star check (live)", checks) else 1)


if __name__ == "__main__":
    main()
