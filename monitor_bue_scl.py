"""
Monitoraggio open-jaw Milano -> Buenos Aires (20-24/12) + Santiago -> Milano (8-11/1).
Gira lun-mar-mer-gio. 20 combinazioni di date; ogni esecuzione ne controlla COMBO_GIORNO
a rotazione (1 credito SerpApi ciascuna): due esecuzioni consecutive coprono tutte le 20.
In caso di alert, 1 credito extra per i dettagli della tratta di ritorno (SerpApi
restituisce solo la prima tratta; la successiva richiede il departure_token).
Budget piano free, mese peggiore (19 giorni lun-gio): (12 + 1) x 19 = 247 <= 250.
Scrive ALERT_SCL.md (il workflow apre una issue) se:
  - il minimo del giorno e' un nuovo minimo storico e <= SOGLIA_SCL, oppure
  - una combinazione cala di almeno CALO_PCT rispetto alla sua ultima rilevazione.
"""
import os, csv, json, datetime as dt, requests

API_KEY      = os.environ["SERPAPI_KEY"]
SOGLIA       = int(os.environ.get("SOGLIA_SCL", 1450))
CALO_PCT     = float(os.environ.get("CALO_PCT", 10))
COMBO_GIORNO = int(os.environ.get("COMBO_GIORNO", 12))
MILANO       = "MXP,LIN,BGY"
ANDATE       = [dt.date(2026, 12, g) for g in range(20, 25)]
RITORNI      = [dt.date(2027, 1, g) for g in range(8, 12)]
COMBO        = [(a, r) for a in ANDATE for r in RITORNI]
CSV_FILE     = "storico_bue_scl.csv"
COLONNE      = ["data", "andata", "ritorno", "prezzo", "compagnie_andata",
                "scali_andata", "durata_andata_h", "partenza"]

oggi = dt.date.today()
if oggi >= ANDATE[0]:
    raise SystemExit("Prima data di partenza raggiunta: monitoraggio concluso.")

if oggi.weekday() > 3:
    raise SystemExit("Oggi non e' tra lunedi' e giovedi': nessuna ricerca.")

# rotazione sul numero di esecuzioni lun-gio (non sui giorni di calendario)
settimane, resto = divmod((oggi - dt.date(2026, 10, 5)).days, 7)   # 5/10/2026 = lunedi'
esecuzione = settimane * 4 + resto
start = (esecuzione * COMBO_GIORNO) % len(COMBO)
da_controllare = [COMBO[(start + i) % len(COMBO)] for i in range(COMBO_GIORNO)]

storico = []
if os.path.exists(CSV_FILE):
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        storico = [{c: r.get(c, "") for c in COLONNE} for r in csv.DictReader(f)]
prev_min = min((int(r["prezzo"]) for r in storico if r["prezzo"]), default=None)
ultimo = {(r["andata"], r["ritorno"]): int(r["prezzo"]) for r in storico if r["prezzo"]}

def cerca(andata, ritorno, token=None):
    tratte = [{"departure_id": MILANO, "arrival_id": "EZE", "date": andata.isoformat()},
              {"departure_id": "SCL", "arrival_id": MILANO, "date": ritorno.isoformat()}]
    params = {"engine": "google_flights", "type": 3, "multi_city_json": json.dumps(tratte),
              "stops": 3, "currency": "EUR", "hl": "it", "gl": "it", "api_key": API_KEY}
    if token:
        params["departure_token"] = token
    return requests.get("https://serpapi.com/search", params=params, timeout=60).json()

def descrivi(opzione):
    righe = []
    for f in opzione["flights"]:
        righe.append(f"  - {f.get('airline', '?')} {f.get('flight_number', '')}: "
                     f"{f['departure_airport']['id']} {f['departure_airport'].get('time', '')} -> "
                     f"{f['arrival_airport']['id']} {f['arrival_airport'].get('time', '')}")
    for l in opzione.get("layovers", []):
        righe.append(f"  - scalo a {l.get('id', '?')}: {round(l.get('duration', 0) / 60, 1)} h")
    righe.append(f"  - durata totale: {round(opzione.get('total_duration', 0) / 60, 1)} h")
    return "\n".join(righe)

nuove, avvisi, segnalate, dettagli = [], [], {}, {}
for andata, ritorno in da_controllare:
    try:
        d = cerca(andata, ritorno)
    except requests.RequestException as e:
        print(f"{andata} / {ritorno}: errore di rete ({e})"); continue
    if "error" in d:
        print(f"{andata} / {ritorno}: errore SerpApi ({d['error']})"); continue
    voli = [v for v in d.get("best_flights", []) + d.get("other_flights", []) if "price" in v]
    if not voli:
        print(f"{andata} / {ritorno}: nessun volo"); continue

    best = min(voli, key=lambda v: v["price"])
    riga = {
        "data": oggi.isoformat(), "andata": andata.isoformat(), "ritorno": ritorno.isoformat(),
        "prezzo": best["price"],
        "compagnie_andata": " + ".join(sorted({f["airline"] for f in best["flights"]})),
        "scali_andata": len(best["flights"]) - 1,
        "durata_andata_h": round(best.get("total_duration", 0) / 60, 1),
        "partenza": best["flights"][0]["departure_airport"]["id"],
    }
    nuove.append(riga)
    dettagli[(riga["andata"], riga["ritorno"])] = (best, d.get("search_metadata", {}).get("google_flights_url"))
    print(f"{andata} / {ritorno}: {riga['prezzo']} EUR ({riga['compagnie_andata']}, "
          f"{riga['scali_andata']} scali andata, {riga['durata_andata_h']} h, da {riga['partenza']})")

    prec = ultimo.get((riga["andata"], riga["ritorno"]))
    if prec:
        calo = (prec - best["price"]) / prec * 100
        if calo >= CALO_PCT:
            segnalate[(riga["andata"], riga["ritorno"])] = best["price"]
            avvisi.append(f"- {andata} / {ritorno}: {best['price']} EUR, calo del {calo:.1f}% (era {prec} EUR)")

if not nuove:
    raise SystemExit("Nessun prezzo ottenuto oggi.")

storico += nuove
with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLONNE)
    w.writeheader(); w.writerows(storico)

m = min(nuove, key=lambda r: r["prezzo"])
if m["prezzo"] <= SOGLIA and (prev_min is None or m["prezzo"] < prev_min):
    segnalate[(m["andata"], m["ritorno"])] = m["prezzo"]
    avvisi.insert(0, f"Nuovo minimo storico: {m['prezzo']} EUR - andata {m['andata']}, ritorno {m['ritorno']}, "
                     f"{m['compagnie_andata']}, partenza da {m['partenza']} (precedente: {prev_min} EUR, soglia {SOGLIA} EUR).")

if avvisi:
    # dettagli completi (andata + ritorno) per la combinazione segnalata piu' economica: 1 credito extra
    chiave = min(segnalate, key=segnalate.get)
    best, url = dettagli[chiave]
    a, r = (dt.date.fromisoformat(x) for x in chiave)
    testo = [f"\n## Dettagli {a} / {r} - {best['price']} EUR totali\n", "**Andata Milano -> Buenos Aires**", descrivi(best)]
    try:
        d2 = cerca(a, r, best.get("departure_token"))
        rit = [v for v in d2.get("best_flights", []) + d2.get("other_flights", []) if "price" in v]
        if "error" in d2 or not rit:
            testo.append(f"\n**Ritorno**: dettagli non disponibili ({d2.get('error', 'nessun volo')})")
        else:
            rb = min(rit, key=lambda v: v["price"])
            testo += [f"\n**Ritorno Santiago -> Milano** (prezzo totale itinerario: {rb['price']} EUR)", descrivi(rb)]
    except requests.RequestException as e:
        testo.append(f"\n**Ritorno**: dettagli non disponibili (errore di rete: {e})")
    if url:
        testo.append(f"\n[Apri la ricerca su Google Flights]({url})")
    with open("ALERT_SCL.md", "w", encoding="utf-8") as f:
        f.write("Milano -> Buenos Aires / Santiago -> Milano\n\n" + "\n".join(avvisi) + "\n" + "\n".join(testo))
