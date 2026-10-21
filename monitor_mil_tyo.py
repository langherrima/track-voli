"""Monitor giornaliero Milano -> Tokyo, A/R 24/12/2026 - 10/01/2027.
Condizioni: max 1 scalo per tratta, scalo max MAX_SCALO_MIN minuti.
Crediti SerpApi: 1 ricerca andata + 1 ricerca ritorno per tentativo (di norma 2 al giorno).
"""
import os, csv, datetime as dt, requests

API_KEY = os.environ["SERPAPI_KEY"]
MAX_SCALO = int(os.environ.get("MAX_SCALO_MIN", 210))   # 3h30
MAX_TENTATIVI = int(os.environ.get("MAX_TENTATIVI", 3))  # andate provate se il ritorno non rispetta i vincoli
STORICO = "storico_mil_tyo.csv"

BASE = dict(
    engine="google_flights", type=1,
    departure_id="MXP,LIN,BGY", arrival_id="HND,NRT",
    outbound_date="2026-12-24", return_date="2027-01-10",
    adults=1, travel_class=1, stops=2,                # stops=2 -> 1 scalo o meno
    layover_duration=f"0,{MAX_SCALO}",
    currency="EUR", hl="it", gl="it", api_key=API_KEY,
)


def cerca(**extra):
    r = requests.get("https://serpapi.com/search.json", params={**BASE, **extra}, timeout=90)
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


andate = sorted(filter(valido, cerca()), key=lambda x: x["price"])
best = None
for a in andate[:MAX_TENTATIVI]:
    ritorni = sorted(filter(valido, cerca(departure_token=a["departure_token"])), key=lambda x: x["price"])
    if ritorni:
        best = (a, ritorni[0])
        break

oggi = dt.date.today().isoformat()
storico = []
if os.path.exists(STORICO):
    with open(STORICO, newline="") as fh:
        storico = list(csv.DictReader(fh))

if not best:
    testo = f"Nessun volo trovato oggi ({oggi}) con max 1 scalo e scalo entro {hm(MAX_SCALO)}."
    titolo = f"MIL-TYO {oggi}: nessun risultato"
else:
    a, r = best
    prezzo = r["price"]                      # nel ritorno il prezzo è il totale A/R
    da, dr = descrivi(a), descrivi(r)
    riga = {"data": oggi, "prezzo": prezzo, **{f"and_{k}": v for k, v in da.items()},
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
    titolo = f"MIL-TYO {oggi}: {prezzo} €"
    testo = (
        f"**Prezzo più basso A/R: {prezzo} €**{var}\n\n"
        f"**Andata 24/12** — {da['compagnie']} ({da['voli']})\n"
        f"{da['da']} {da['partenza']} → {da['a']} {da['arrivo']} · scalo {da['scalo']} · durata {da['durata']}\n\n"
        f"**Ritorno 10/01** — {dr['compagnie']} ({dr['voli']})\n"
        f"{dr['da']} {dr['partenza']} → {dr['a']} {dr['arrivo']} · scalo {dr['scalo']} · durata {dr['durata']}\n"
    )

with open("ALERT_TYO.md", "w") as fh:
    fh.write(testo)
with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
    fh.write(f"titolo={titolo}\n")
print(titolo)
