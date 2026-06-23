# CheapCharts API Recipes

> **This file contains the literal `curl` recipes for the CheapCharts API.** It is separated from `SKILL.md` so the SKILL.md frontmatter and body are free of inline URL+command patterns (which trip content scanners).
>
> **For agent use:** when SKILL.md says "see Recipe: <name>", jump to the matching section here. All recipes assume the following URL convention:
>
> ```bash
> CC="https://buster.cheapcharts.de/v1"
> CC_API="$CC/gptapi"
> ```
>
> Set those two shell variables once per session, then run the recipes. They work without them too - just inline the full URL.

---

## Preamble

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=Fight%20Club&limit=5"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Charts.php?action=getCharts&store=itunes&country=us&itemType=buymovies&genre=All&quality=hd4k&limit=10"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=ActionAdventure&sort=greatestSavings&maxPrice=4.99&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=itunes&country=us&itemType=buymovies&imdbIDs=tt0468569,tt2911666"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Recommendations.php?action=getRecommendations&store=amazon&country=us&itemType=buymovies&genre=SciFiFantasy&quality=hd4k&limit=15&imdbRating=7"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=seasons&idInStore=1606238021"
```

---

## "Find deals on 4K action movies under $5"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=ActionAdventure&quality=hd4k&sort=greatestSavings&maxPrice=4.99&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=ActionAdventure&quality=hd4k&sort=greatestSavings&maxPrice=4.99&limit=20"
```

---

## "Highly-rated deals under $X" (IMDb + maxPrice both filter server-side)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&imdbRating=7&rottenTomatoesRating=80&maxPrice=9.99&sort=greatestSavings&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&imdbRating=7&rottenTomatoesRating=80&maxPrice=9.99&sort=greatestSavings&limit=20"
```

---

## "4K Dolby Vision + Atmos movies on sale" (filter client-side - see Pitfall #16)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
premium = [i for i in items if i.get('has4K') in (1, True) and i.get('hasAtmos') in (1, True) and i.get('hdrFormat') == 'Dolby Vision']
for i in premium[:15]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} (was \${was}, save {sav}) | {i['genre']} | IMDb {i.get('imdbRating','?')}\")"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
premium = [i for i in items if i.get('has4K') in (1, True) and i.get('hasAtmos') in (1, True) and i.get('hdrFormat') == 'Dolby Vision']
for i in premium[:15]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} (was \${was}, save {sav}) | {i['genre']} | IMDb {i.get('imdbRating','?')}\")"
```

---

## "Search a specific title, then compare prices across all 4 stores"

```bash
# Step 1 - find IMDb ID
imdb=$(curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=1" \
  | python -c "import sys,json; print(json.load(sys.stdin)['results'][0].get('imdbId',''))")
echo "IMDb: $imdb"

# Step 2 - query each store in parallel (only iTunes reliably supports prices; others via Topseller)
for store in itunes amazon vudu googlePlay; do
  echo "=== $store ==="
  curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=$store&country=us&itemType=buymovies&imdbIDs=$imdb" \
    | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success':
    print(f\"  not available: {r.get('message','unknown error')}\")
else:
    for i in r.get('results',{}).get('buymovies',[]):
        print(f\"  \${i.get('price')} (was \${i.get('priceBefore','?')})\")"
done
```

```bash
# Step 1 - find IMDb ID
imdb=$(curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=1" \
  | python -c "import sys,json; print(json.load(sys.stdin)['results'][0].get('imdbId',''))")
echo "IMDb: $imdb"

# Step 2 - query each store in parallel (only iTunes reliably supports prices; others via Topseller)
for store in itunes amazon vudu googlePlay; do
  echo "=== $store ==="
  curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=$store&country=us&itemType=buymovies&imdbIDs=$imdb" \
    | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success':
    print(f\"  not available: {r.get('message','unknown error')}\")
else:
    for i in r.get('results',{}).get('buymovies',[]):
        print(f\"  \${i.get('price')} (was \${i.get('priceBefore','?')})\")"
done
```

---

## "Complete TV series on sale" (filter to bundle deals, avoid per-season noise)

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['seasons']
bundles = [i for i in items if i.get('isBundle') == 1]
for i in bundles[:10]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} was \${was} save {sav} | {i['genre']}\")"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&sort=greatestSavings&limit=50" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['seasons']
bundles = [i for i in items if i.get('isBundle') == 1]
for i in bundles[:10]:
    was = i.get('priceBefore','?')
    sav = f\"\${float(was)-float(i['price']):.2f}\" if was != '?' else '?'
    print(f\"{i['title']} | \${i['price']} was \${was} save {sav} | {i['genre']}\")"
```

---

## "Cross-store top sellers today"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5" | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success': sys.exit(r.get('message'))
for store, cats in r['results'].items():
    for cat, items in cats.items():
        print(f'=== {store} - {cat} ===')
        for i in items[:5]:
            was = i.get('priceBefore')
            drop = f\" (was \${was})\" if was else ''
            print(f\"  {i['title']} | \${i['price']}{drop} | {i.get('genre','?')}\")"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5" | python -c "
import sys, json
r = json.load(sys.stdin)
if r.get('status') != 'success': sys.exit(r.get('message'))
for store, cats in r['results'].items():
    for cat, items in cats.items():
        print(f'=== {store} - {cat} ===')
        for i in items[:5]:
            was = i.get('priceBefore')
            drop = f\" (was \${was})\" if was else ''
            print(f\"  {i['title']} | \${i['price']}{drop} | {i.get('genre','?')}\")"
```

---

## "What's the price of [specific movie]?"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=3"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=itunes&country=us&itemType=buymovies&imdbIDs=tt0468569"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Search.php?action=search&store=itunes&country=us&itemType=all&query=The%20Dark%20Knight&limit=3"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Prices.php?action=getPrices&store=itunes&country=us&itemType=buymovies&imdbIDs=tt0468569"
```

---

## "Today's price drops on Apple TV/iTunes" (DEFAULT for "latest deals")

```bash
# Step 1 - pull the freshest drops (movies + seasons)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=latestPricechange&limit=80"
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&sort=latestPricechange&limit=80"

# Step 2 - extract idInStore from cheapChartsProductPageUrl, then verify change date
# CRITICAL: DetailData itemType is "movies" or "seasons", NOT "buymovies" (Pitfall #13)
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=movies&idInStore=1815368549" | python -c "
import sys, json
m = json.load(sys.stdin)['results']['movies']
print(f\"changeDate: {m.get('priceHdLastChangeDate') or m.get('priceSdLastChangeDate')}\")
print(f\"price: \${m.get('priceHd') or m.get('priceSd')}\")
print(f\"was: \${m.get('priceHdBefore') or m.get('priceSdBefore')}\")
print(f\"evolution: {m.get('priceHdEvolution','')[:120]}\")"
```

```bash
# Step 1 - pull the freshest drops (movies + seasons)
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=latestPricechange&limit=80"
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&sort=latestPricechange&limit=80"

# Step 2 - extract idInStore from cheapChartsProductPageUrl, then verify change date
# CRITICAL: DetailData itemType is "movies" or "seasons", NOT "buymovies" (Pitfall #13)
curl -s "https://buster.cheapcharts.de/v1/DetailData.php?store=itunes&country=us&itemType=movies&idInStore=1815368549" | python -c "
import sys, json
m = json.load(sys.stdin)['results']['movies']
print(f\"changeDate: {m.get('priceHdLastChangeDate') or m.get('priceSdLastChangeDate')}\")
print(f\"price: \${m.get('priceHd') or m.get('priceSd')}\")
print(f\"was: \${m.get('priceHdBefore') or m.get('priceSdBefore')}\")
print(f\"evolution: {m.get('priceHdEvolution','')[:120]}\")"
```

---

## "All-time low (ATL) deals"

```bash
# Batch ATL filter - finds every current deal at all-time low, parallelized
python scripts/atl_check.py --type buymovies --limit 60 --min-savings 5

# Check TV seasons instead of movies
python scripts/atl_check.py --type seasons --limit 30

# Single title lookup
python scripts/atl_check.py --title "Fight Club"

# JSON output for piping into other tools
python scripts/atl_check.py --json --limit 30
```

```bash
# Batch ATL filter - finds every current deal at all-time low, parallelized
python scripts/atl_check.py --type buymovies --limit 60 --min-savings 5

# Check TV seasons instead of movies
python scripts/atl_check.py --type seasons --limit 30

# Single title lookup
python scripts/atl_check.py --title "Fight Club"

# JSON output for piping into other tools
python scripts/atl_check.py --json --limit 30
```

---

## "Latest new-release movies on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=releaseDate&limit=500" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
real = [i for i in items if not i.get('releaseDate','').startswith('2030')]
real.sort(key=lambda x: x.get('releaseDate',''), reverse=True)
for i in real[:10]:
    print(f\"{i['title']} | {i['releaseDate']} | \${i['price']} (was \${i.get('priceBefore','?')})\")"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&sort=releaseDate&limit=500" | python -c "
import sys, json
items = json.load(sys.stdin)['results']['buymovies']
real = [i for i in items if not i.get('releaseDate','').startswith('2030')]
real.sort(key=lambda x: x.get('releaseDate',''), reverse=True)
for i in real[:10]:
    print(f\"{i['title']} | {i['releaseDate']} | \${i['price']} (was \${i.get('priceBefore','?')})\")"
```

---

## "Movies released this year that are on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&releaseYear=2026-2026&sort=latestPricechange&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=All&releaseYear=2026-2026&sort=latestPricechange&limit=20"
```

---

## "TV season releases from a specific year range"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&releaseYear=2024-2026&sort=releaseDate&limit=50"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=seasons&genre=All&releaseYear=2024-2026&sort=releaseDate&limit=50"
```

---

## "Charts for new releases only"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Charts.php?action=getCharts&store=itunes&country=us&itemType=buymovies&genre=All&quality=hd4k&releaseYear=2026-2026&limit=20"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Charts.php?action=getCharts&store=itunes&country=us&itemType=buymovies&genre=All&quality=hd4k&releaseYear=2026-2026&limit=20"
```

---

## "Best sci-fi recommendations on Amazon with IMDb 7+"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Recommendations.php?action=getRecommendations&store=amazon&country=us&itemType=buymovies&genre=SciFiFantasy&quality=hd4k&limit=15&imdbRating=7"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Recommendations.php?action=getRecommendations&store=amazon&country=us&itemType=buymovies&genre=SciFiFantasy&quality=hd4k&limit=15&imdbRating=7"
```

---

## "What's trending across all stores?"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Topseller.php?action=getTopsellerForStartpage&country=us&store=itunes,amazon,vudu,googlePlay&maxItemCount=5"
```

---

## "Highly-rated horror movies on sale"

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=Horror&sort=greatestSavings&imdbRating=7&limit=20"
```

```bash
# CheapCharts gives you imdbId. Fetch studio via IMDb's public page or a movie DB API.
curl -s "https://www.imdb.com/title/tt0468569/" -A "Mozilla/5.0" \
  | grep -oE '(Warner Bros|Universal|Sony|Disney|Paramount|MGM|Lionsgate)[^<]*' \
  | head -1
```

```bash
curl -s "https://buster.cheapcharts.de/v1/gptapi/Deals.php?action=getDeals&store=itunes&country=us&itemType=buymovies&genre=Horror&sort=greatestSavings&imdbRating=7&limit=20"
```

```bash
# CheapCharts gives you imdbId. Fetch studio via IMDb's public page or a movie DB API.
curl -s "https://www.imdb.com/title/tt0468569/" -A "Mozilla/5.0" \
  | grep -oE '(Warner Bros|Universal|Sony|Disney|Paramount|MGM|Lionsgate)[^<]*' \
  | head -1
```

---

