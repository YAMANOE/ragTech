"""Inspect the ng-repeat result row template in lob_02_after_search.html"""
from pathlib import Path
from bs4 import BeautifulSoup

html = Path("data/indexes/lob_02_after_search.html").read_text(encoding="utf-8")
soup = BeautifulSoup(html, "html.parser")

# Find the ng-repeat for SearchResult
items = soup.find_all(attrs={"ng-repeat": lambda x: x and "SearchResult" in x})
print("ng-repeat SearchResult elements: " + str(len(items)))
for el in items[:5]:
    print("TAG: " + el.name + "  attrs: " + str(el.attrs))
    print("HTML:\n" + str(el)[:600])
    print("---")

# Also look for elements with ng-click that navigate
clickers = soup.find_all(attrs={"ng-click": lambda x: x and ("Legislation" in x or "Detail" in x or "View" in x or "navigate" in x.lower())})
print("\n\nng-click navigation elements: " + str(len(clickers)))
for el in clickers[:10]:
    print("TAG: " + el.name + "  ng-click: " + str(el.get("ng-click")) + "  text: " + el.get_text(strip=True)[:60])
print()

# Find ALL ng-click attributes
all_clickers = soup.find_all(attrs={"ng-click": True})
unique_clicks = set(el.get("ng-click") for el in all_clickers)
print("All unique ng-click expressions: " + str(unique_clicks))
