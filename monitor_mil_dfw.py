"""Monitor giornaliero multi-city: Milano -> Dallas 22/12/2026, New Orleans -> Milano 08/01/2027.
Condizioni: max 1 scalo per tratta, scalo max MAX_SCALO_MIN minuti.
Crediti SerpApi: 1 ricerca prima tratta + 1 per ciascuna delle MAX_TENTATIVI andate provate (di norma 4 al giorno).
"""
import os, csv, json, datetime as dt, requests

API_KEY = os.environ["SERPAPI_KEY"]
MAX_SCALO = int(os.environ.get("MAX_SCALO_MIN", 180))   # 3h00
MAX_TENTATIVI = int(os.environ.get("MAX_TENTATIVI", 3))  # andate di cui si cerca il ritorno
STORICO = "storico_mil_dfw.csv"

# Se oggi ha già girato (es. il cron di riserva) esce senza usare crediti
OGGI = dt.date.today().isoformat()
if os.path.exists(STORICO):
    with open(STORICO, newline="") as fh:
        if any(r.get("data") == OGGI for r in csv.DictReader(fh)):
            print("Già eseguito oggi:", OGGI)
            raise SystemExit(0)

TRATTE = [
    {"departure_id": "MXP,LIN,BGY", "arrival_id": "DFW,DAL", "date": "2026-12-22"},
    {"departure_id": "MSY", "arrival_id": "MXP,LIN,BGY", "date": "2027-01-08"},
]
BASE = dict(
    engine="google_flights", type=3, multi_city_json=json.dumps(TRATTE),
    adults=1, travel_class=1, stops=2,                # stops=2 -> 1 scalo o meno
    layover_duration=f"0,{MAX_SCALO}", deep_search="true",
    currency="EUR", hl="it", gl="it", api_key=API_KEY,
)


def cerca(**extra):
    r = requests.get("https://serpapi.com/search.json", params={**BASE, **extra}, timeout=180)
    d = r.json()
    if "error" in d:
        print("SerpApi:", d["error"])
        return []
    return d.get("best_flights", []) + d.get("other_flights", [])


def valido(it):
    lay = it.get("layovers", [])
    return "price" in it and len(lay) <= 1 and all(l.get("duration", 0) <= MAX_SCALO for l in lay)


def hm(m):
    return f"{m // 60}h{m % 60:02d}"


def descrivi(it):
    f = it["flights"]
    lay = it.get("layovers", [])
    return dict(
        compagnie=" + ".join(dict.fromkeys(x["airline"] for x in f)),
        voli=" / ".join(x.get("flight_number", "") for x in f),
        da=f[0]["departure_airport"]["id"], a=f[-1]["arrival_airport"]["id"],
        partenza=f[0]["departure_airport"]["time"], arrivo=f[-1]["arrival_airport"]["time"],
        scalo=" ".join(f'{l["id"]} {hm(l["duration"])}' for l in lay) or "diretto",
        durata=hm(it["total_duration"]),
    )


# Prova le MAX_TENTATIVI andate più economiche e tiene il totale più basso (non la prima trovata)
andate = sorted(filter(valido, cerca()), key=lambda x: x["price"])
best = None
for a in andate[:MAX_TENTATIVI]:
    ritorni = sorted(filter(valido, cerca(departure_token=a["departure_token"])), key=lambda x: x["price"])
    if ritorni and (best is None or ritorni[0]["price"] < best[1]["price"]):
        best = (a, ritorni[0])

storico = []
if os.path.exists(STORICO):
    with open(STORICO, newline="") as fh:
        storico = list(csv.DictReader(fh))

elenco = "\n".join(f"- {x['price']} € — {descrivi(x)['compagnie']} · {descrivi(x)['da']} → {descrivi(x)['a']} · "
                   f"scalo {descrivi(x)['scalo']}" for x in andate[:5])
elenco = f"\n**Andate più economiche trovate (prezzo totale indicativo):**\n{elenco}\n" if elenco else ""

if not best:
    testo = f"Nessun itinerario trovato oggi ({OGGI}) con max 1 scalo e scalo entro {hm(MAX_SCALO)}.\n{elenco}"
    titolo = f"MIL-DFW/MSY {OGGI}: nessun risultato"
else:
    a, r = best
    prezzo = r["price"]                      # nell'ultima tratta il prezzo è il totale dell'itinerario
    da, dr = descrivi(a), descrivi(r)
    riga = {"data": OGGI, "prezzo": prezzo, **{f"and_{k}": v for k, v in da.items()},
            **{f"rit_{k}": v for k, v in dr.items()}}
    prec = [int(x["prezzo"]) for x in storico]
    var = ""
    if prec:
        diff = prezzo - prec[-1]
        var = f" ({'+' if diff > 0 else ''}{diff} € da ieri; minimo visto {min(prec + [prezzo])} €)"
    storico.append(riga)
    with open(STORICO, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(riga.keys()))
        w.writeheader()
        w.writerows(storico)
    titolo = f"MIL-DFW/MSY {OGGI}: {prezzo} €"
    testo = (
        f"**Prezzo più basso totale: {prezzo} €**{var}\n\n"
        f"**Andata 22/12 Milano → Dallas** — {da['compagnie']} ({da['voli']})\n"
        f"{da['da']} {da['partenza']} → {da['a']} {da['arrivo']} · scalo {da['scalo']} · durata {da['durata']}\n\n"
        f"**Ritorno 08/01 New Orleans → Milano** — {dr['compagnie']} ({dr['voli']})\n"
        f"{dr['da']} {dr['partenza']} → {dr['a']} {dr['arrivo']} · scalo {dr['scalo']} · durata {dr['durata']}\n"
        f"{elenco}"
    )

with open("ALERT_DFW.md", "w") as fh:
    fh.write(testo)
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
    fh.write(f"titolo={titolo}\n")
print(titolo)
