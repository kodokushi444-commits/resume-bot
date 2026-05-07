from __future__ import annotations

import argparse
import sys

import requests
from bs4 import BeautifulSoup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    query = " ".join(args.query).strip()
    response = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss"},
        headers={
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=20,
    )
    response.raise_for_status()
    safe_print(str(response.status_code))
    safe_print(response.url)
    safe_print(response.text[:1000])
    soup = BeautifulSoup(response.text, "xml")
    items = soup.find_all("item")
    safe_print(f"ITEMS={len(items)}")
    for item in items[: max(args.limit, 0)]:
        title = item.find("title").get_text(" ", strip=True) if item.find("title") else ""
        link = item.find("link").get_text(" ", strip=True) if item.find("link") else ""
        description = item.find("description").get_text(" ", strip=True) if item.find("description") else ""
        safe_print(f"TITLE={title}")
        safe_print(f"LINK={link}")
        safe_print(f"DESC={description[:300]}")
        safe_print("---")
    return 0


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("gbk", errors="replace"))


if __name__ == "__main__":
    raise SystemExit(main())
