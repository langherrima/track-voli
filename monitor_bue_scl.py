"""
Monitoraggio open-jaw Milano -> Buenos Aires (20-24/12) + Santiago -> Milano (8-11/1).
Gira lun-mar-mer-gio. 20 combinazioni di date; ogni esecuzione ne controlla COMBO_GIORNO
a rotazione (1 credito SerpApi ciascuna): due esecuzioni consecutive coprono tutte le 20.
In caso di alert, 1 credito extra per i dettagli della tratta di ritorno (SerpApi
restituisce solo la prima tratta; la successiva richiede il departure_token).
Budget piano free, mese peggiore (19 giorni lun-gio): (12 + 1) x 19 = 247 <= 250.
Scrive ogni giorno ALERT_SCL.md (il workflow apre una issue). Nel titolo compare "ALERT" se:
  - il minimo del giorno e' un nuovo minimo storico e <= SOGLIA_SCL, oppure
  - una combinazione cala di almeno CALO_PCT rispetto alla sua ultima rilevazione.
L'issue contiene UN solo itinerario, il migliore del giorno: tra tutte le opzioni con prezzo
entro TOLLERANZA_PCT dal minimo, quella con durata piu' breve (stesso criterio per il ritorno).
"""
import os, csv, json, datetime as dt, requests

API_KEY      = os.environ["SERPAPI_KEY"]
SOGLIA       = int(os.environ.get("SOGLIA_SCL", 1450))
CALO_PCT     = float(os.environ.get("CALO_PCT", 10))
COMBO_GIORNO = int(os.environ.get("COMBO_GIORNO", 12))
MAX_ORE      = float(os.environ.get("MAX_ORE", 24))   # durata massima per tratta, scali inclusi
TOLLERANZA   = float(os.environ.get("TOLLERANZA_PCT", 5))
MILANO       = "MXP,LIN,BGY"
ANDATE       = [dt.date(2026, 12, g) for g in range(20, 25)]
RITORNI      = [dt.date(2027, 1, g) for g in range(8, 12)]
COMBO        = [(a, r) for a in ANDATE for r in RITORNI]
CSV_FILE     = "storico_bue_scl.csv"
CSV_GIORNO   = "storico_migliore_giorno.csv"
COL_GIORNO   = ["data", "alert", "andata", "ritorno", "prezzo_totale",
                "compagnie_andata", "scali_andata", "durata_andata_h", "partenza",
                "compagnie_ritorno", "scali_ritorno", "durata_ritorno_h", "arrivo",
                "minimo_2_esecuzioni", "date_minimo_2_esecuzioni"]
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
                     f"{f['departure_airport']['id']} -> {f['arrival_airport']['id']}")
    for l in opzione.get("layovers", []):
        righe.append(f"  - scalo a {l.get('id', '?')}: {round(l.get('duration', 0) / 60, 1)} h")
    righe.append(f"  - durata totale: {round(opzione.get('total_duration', 0) / 60, 1)} h")
    return "\n".join(righe)

def entro_limite(opzioni):
    return [o for o in opzioni if "price" in o and 0 < (o.get("total_duration") or 0) <= MAX_ORE * 60]

def migliore(opzioni):
    """Piu' breve tra le opzioni con prezzo entro TOLLERANZA_PCT dal minimo."""
    tetto = min(o["price"] for o in opzioni) * (1 + TOLLERANZA / 100)
    return min((o for o in opzioni if o["price"] <= tetto),
               key=lambda o: (o.get("total_duration") or 10**6, o["price"]))

nuove, motivi, candidati, cali = [], [], [], []
for andata, ritorno in da_controllare:
    try:
        d = cerca(andata, ritorno)
    except requests.RequestException as e:
        print(f"{andata} / {ritorno}: errore di rete ({e})"); continue
    if "error" in d:
        print(f"{andata} / {ritorno}: errore SerpApi ({d['error']})"); continue
    voli = entro_limite(d.get("best_flights", []) + d.get("other_flights", []))
    if not voli:
        print(f"{andata} / {ritorno}: nessun volo entro {MAX_ORE:g} h"); continue

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
    url = d.get("search_metadata", {}).get("google_flights_url")
    candidati += [dict(v, _andata=andata, _ritorno=ritorno, _url=url) for v in voli]
    print(f"{andata} / {ritorno}: {riga['prezzo']} EUR ({riga['compagnie_andata']}, "
          f"{riga['scali_andata']} scali andata, {riga['durata_andata_h']} h, da {riga['partenza']})")

    prec = ultimo.get((riga["andata"], riga["ritorno"]))
    if prec:
        calo = (prec - best["price"]) / prec * 100
        if calo >= CALO_PCT:
            cali.append(calo)

if not nuove:
    raise SystemExit("Nessun prezzo ottenuto oggi.")

storico += nuove
with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLONNE)
    w.writeheader(); w.writerows(storico)

m = min(nuove, key=lambda r: r["prezzo"])
if m["prezzo"] <= SOGLIA and (prev_min is None or m["prezzo"] < prev_min):
    motivi.append(f"nuovo minimo storico {m['prezzo']} EUR" + (f" (prima {prev_min} EUR)" if prev_min else ""))
if cali:
    motivi.append(f"calo prezzi fino a -{max(cali):.0f}% su {len(cali)} combinazioni")

if True:
    # un solo itinerario: il migliore del giorno; 1 credito extra per i dettagli del ritorno
    best = migliore(candidati)
    a, r, url = best["_andata"], best["_ritorno"], best["_url"]
    testo = [f"Motivo alert: {'; '.join(motivi)}." if motivi else "Nessun alert oggi: report quotidiano.",
             f"\n## Migliore di oggi: andata {a}, ritorno {r}\n",
             f"**Andata Milano -> Buenos Aires**", descrivi(best)]
    totale, rb = best["price"], None
    try:
        d2 = cerca(a, r, best.get("departure_token"))
        rit = entro_limite(d2.get("best_flights", []) + d2.get("other_flights", []))
        if "error" in d2 or not rit:
            testo.append(f"\n**Ritorno**: dettagli non disponibili ({d2.get('error', f'nessun volo entro {MAX_ORE:g} h')})")
        else:
            rb = migliore(rit)
            totale = rb["price"]
            testo += ["\n**Ritorno Santiago -> Milano**", descrivi(rb)]
    except requests.RequestException as e:
        testo.append(f"\n**Ritorno**: dettagli non disponibili (errore di rete: {e})")
    sintesi = lambda o: (" + ".join(sorted({f["airline"] for f in o["flights"]})),
                         len(o["flights"]) - 1, round(o.get("total_duration", 0) / 60, 1))
    ca, sa, da = sintesi(best)
    cr, sr, dr = sintesi(rb) if rb else ("", "", "")
    # minimo puro su tutte le 20 combinazioni: esecuzione precedente + oggi (oggi prevale)
    date_prec = [x["data"] for x in storico if x["data"] < oggi.isoformat()]
    copertura = {(x["andata"], x["ritorno"]): int(x["prezzo"])
                 for x in storico if date_prec and x["data"] == max(date_prec) and x["prezzo"]}
    copertura.update({(x["andata"], x["ritorno"]): int(x["prezzo"]) for x in nuove})
    k2 = min(copertura, key=copertura.get)
    nuovo = not os.path.exists(CSV_GIORNO)
    with open(CSV_GIORNO, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COL_GIORNO)
        if nuovo: w.writeheader()
        w.writerow({"data": oggi.isoformat(), "alert": "si" if motivi else "no",
                    "andata": a.isoformat(), "ritorno": r.isoformat(), "prezzo_totale": totale,
                    "compagnie_andata": ca, "scali_andata": sa, "durata_andata_h": da,
                    "partenza": best["flights"][0]["departure_airport"]["id"],
                    "compagnie_ritorno": cr, "scali_ritorno": sr, "durata_ritorno_h": dr,
                    "arrivo": rb["flights"][-1]["arrival_airport"]["id"] if rb else "",
                    "minimo_2_esecuzioni": copertura[k2],
                    "date_minimo_2_esecuzioni": f"{k2[0]} / {k2[1]}"})
    testo.insert(1, f"\n**Prezzo totale: {totale} EUR**")
    if url:
        testo.append(f"\n[Apri su Google Flights]({url})")
    with open("ALERT_SCL.md", "w", encoding="utf-8") as f:
        f.write("\n".join(testo) + "\n")
    titolo = f"{'ALERT - ' if motivi else ''}Volo BUE/SCL {oggi}: {totale} EUR ({a.strftime('%d/%m')} - {r.strftime('%d/%m')})"
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"titolo={titolo}\n")
