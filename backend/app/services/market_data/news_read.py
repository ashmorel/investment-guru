"""Read-side news helpers: normalize/dedupe headlines and rank holdings. Pure
functions (no DB/IO) so they're trivially unit-tested."""
import re

from app.models import NewsItem

_PUNCT = re.compile(r"[^0-9a-z ]+")
_WS = re.compile(r"\s+")


def norm_title(title: str) -> str:
    t = _PUNCT.sub(" ", title.lower())
    return _WS.sub(" ", t).strip()


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Collapse near-duplicate headlines by normalized title (keep the
    earliest-published of a duplicate set), return newest-first."""
    def sort_key(n: NewsItem):
        return (n.published_at or n.fetched_at)

    best: dict[str, NewsItem] = {}
    for n in items:
        k = norm_title(n.title)
        cur = best.get(k)
        if cur is None or sort_key(n) < sort_key(cur):
            best[k] = n
    return sorted(best.values(), key=sort_key, reverse=True)


def rank_groups(groups: list[dict]) -> list[dict]:
    """Order holdings by recent-headline count desc, then latest_published_at desc."""
    return sorted(
        groups,
        key=lambda g: (len(g["items"]), g.get("latest_published_at") or ""),
        reverse=True,
    )
