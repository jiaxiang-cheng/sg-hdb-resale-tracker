#!/usr/bin/env python3
"""Refresh the embedded HDB resale snapshot in index.html from data.gov.sg.

Usage:
  python fetch_data.py                      # download via data.gov.sg API, rewrite index.html
  python fetch_data.py --csv a.csv b.csv    # use CSVs you downloaded manually (no network)
  python fetch_data.py --min-sqm 100 --since 2017-01

Only standard library is needed. Keeps flats strictly larger than --min-sqm
(all prices, so medians and budget headroom are not truncated; the page's
budget slider does the filtering). Data licence: Singapore Open Data Licence.

Endpoints (from the data.gov.sg developer guide; collection 189 = "Resale Flat Prices"):
  collection metadata : https://api-production.data.gov.sg/v2/public/api/collections/189/metadata
  dataset metadata    : https://api-production.data.gov.sg/v2/public/api/datasets/{id}/metadata
  download            : https://api-open.data.gov.sg/v1/public/api/datasets/{id}/initiate-download
                        https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download
The two hostnames above are the part I could not confirm from the guide text; if one
404s, check https://guide.data.gov.sg or just download the CSVs and use --csv.
Public API quota is 5 requests/minute, hence the pauses.
"""
import argparse, csv, datetime, io, json, re, sys, time, urllib.request

COLLECTION = 189
# Known registration-date datasets (confirmed on data.gov.sg): Jan 2017 onwards, and 2015-2016.
KNOWN = ["d_8b84c4ee58e3cfc0ece0d773c8ca6abc", "d_ea9ed51da2787afaf8e51f827c304208"]
API = "https://api-production.data.gov.sg/v2/public/api"
OPEN = "https://api-open.data.gov.sg/v1/public/api"


def get(url, pause=13):
    time.sleep(pause)
    req = urllib.request.Request(url, headers={"User-Agent": "hdb-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def discover():
    """Registration-date datasets in the collection (excludes the pre-2012 approval-date series)."""
    ids = list(KNOWN)
    try:
        meta = json.loads(get(f"{API}/collections/{COLLECTION}/metadata"))
        for d in meta["data"]["childDatasets"]:
            if d in ids:
                continue
            name = json.loads(get(f"{API}/datasets/{d}/metadata"))["data"].get("name", "")
            if "registration" in name.lower():
                ids.append(d)
    except Exception as e:
        print(f"warning: collection discovery failed ({e}); using known IDs only", file=sys.stderr)
    return ids


def download(dataset_id):
    get(f"{OPEN}/datasets/{dataset_id}/initiate-download")
    for _ in range(20):
        res = json.loads(get(f"{OPEN}/datasets/{dataset_id}/poll-download"))
        url = (res.get("data") or {}).get("url")
        if url:
            return get(url, pause=1).decode("utf-8-sig")
        time.sleep(10)
    raise RuntimeError(f"download not ready for {dataset_id}")


def norm(h):
    return re.sub(r"[^a-z]", "", h.lower())


def rows_from(text):
    rd = csv.DictReader(io.StringIO(text))
    for r in rd:
        r = {norm(k): (v or "").strip() for k, v in r.items()}
        try:
            yield dict(ym=r["month"], t=r["town"], f=r["flattype"], b=r["block"], s=r["streetname"],
                       st=r["storeyrange"], a=float(r["floorareasqm"]), m=r["flatmodel"],
                       lc=int(r["leasecommencedate"]), p=int(float(r["resaleprice"])))
        except (KeyError, ValueError):
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="*", help="local CSV files instead of the API")
    ap.add_argument("--html", default="index.html")
    ap.add_argument("--min-sqm", type=float, default=100.0, help="keep floor area strictly greater than this")
    ap.add_argument("--since", default="2012-03", help="earliest month, YYYY-MM (registration-date data starts 2012-03)")
    a = ap.parse_args()

    texts = [open(p, encoding="utf-8-sig").read() for p in a.csv] if a.csv else [download(i) for i in discover()]
    seen, keep = set(), []
    for t in texts:
        for r in rows_from(t):
            key = (r["ym"], r["t"], r["b"], r["s"], r["st"], r["a"], r["p"])
            if r["a"] > a.min_sqm and r["ym"] >= a.since and key not in seen:
                seen.add(key)
                keep.append(r)
    if not keep:
        sys.exit("No rows kept. Check the CSV columns or --min-sqm / --since.")

    L = {k: sorted({r[k] for r in keep}) for k in ("t", "f", "m", "st")}
    ix = {k: {v: i for i, v in enumerate(L[k])} for k in L}
    keep.sort(key=lambda r: r["ym"])
    snap = dict(
        updated=datetime.date.today().isoformat(),
        L=dict(t=L["t"], f=L["f"], m=L["m"], s=L["st"]),
        R=[[r["ym"], ix["t"][r["t"]], ix["f"][r["f"]], ix["m"][r["m"]], r["b"], r["s"],
            ix["st"][r["st"]], r["a"], r["lc"], r["p"]] for r in keep],
    )
    payload = json.dumps(snap, separators=(",", ":"), ensure_ascii=False)
    html = open(a.html, encoding="utf-8").read()
    new, n = re.subn(r"/\*SNAP_START\*/.*?/\*SNAP_END\*/",
                     lambda m: "/*SNAP_START*/" + payload + "/*SNAP_END*/", html, count=1, flags=re.S)
    if n != 1:
        sys.exit("SNAP_START/SNAP_END markers not found in HTML.")
    open(a.html, "w", encoding="utf-8").write(new)
    print(f"Embedded {len(keep):,} sales ({keep[0]['ym']} to {keep[-1]['ym']}), {len(payload)/1e6:.1f} MB payload.")


if __name__ == "__main__":
    main()
