# Mealie Picnic Bridge

FastAPI service die je Mealie boodschappenlijst synchroniseert naar je Picnic winkelmandje.

## Hoe het werkt

1. Haalt alle items op van je Mealie shopping lists
2. Parsed ingredientnamen (gebruikt Mealie's food name, met ingredient-parser-nlp als fallback)
3. Zoekt elk product op bij Picnic via fuzzy matching (rapidfuzz)
4. Slaat de Picnic product mapping op in Mealie's `food.extras` (cache)
5. Voegt producten toe aan je Picnic mandje

## Setup

```bash
cp .env.example .env
# Vul je credentials in
docker compose up --build
```

De service draait op `http://localhost:8080`.

## Endpoints

| Methode | Path      | Beschrijving                           |
|---------|-----------|----------------------------------------|
| GET     | `/`       | Web UI met sync knop                   |
| POST    | `/sync`   | Start synchronisatie                   |
| GET     | `/status` | Laatste sync resultaten                |

## Environment variables

| Variabele             | Verplicht | Default | Beschrijving                  |
|-----------------------|-----------|---------|-------------------------------|
| `MEALIE_HOST`         | Ja        | -       | Mealie URL (bijv. `http://mealie:9000`) |
| `MEALIE_TOKEN`        | Ja        | -       | Mealie API Bearer token       |
| `PICNIC_USERNAME`     | Ja        | -       | Picnic account email          |
| `PICNIC_PASSWORD`     | Ja        | -       | Picnic wachtwoord             |
| `PICNIC_COUNTRY_CODE` | Nee       | `NL`    | Land code                     |
| `FUZZY_THRESHOLD`     | Nee       | `65`    | Minimum fuzzy match score (0-100) |

## Netwerk

De `docker-compose.yml` verwacht een bestaand Docker netwerk `mealie_default`. Dit is het netwerk waar Mealie op draait. Als je netwerk anders heet, pas dit aan in `docker-compose.yml`.
