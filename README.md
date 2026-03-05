# AI Visibility Tracker

Nástroj pro sledování toho, jak jazykové modely (LLM) zmiňují vaši značku při odpovídání na dotazy uživatelů. Měří viditelnost, sentiment a přítomnost konkurence v průběhu času.

---

## Co aplikace dělá

Když uživatelé pokládají AI asistentům otázky jako *„Jaké jsou nejlepší značky elektromobilů?"*, vaše značka se v odpovědi může nebo nemusí objevit. Tato aplikace takové sledování automatizuje ve velkém.

**Základní průběh:**
1. Zadáte svou značku a prompt (otázku, kterou by potenciální zákazník mohl položit AI)
2. Aplikace odešle tento prompt LLM a zachytí surovou odpověď
3. Druhý LLM průchod analyzuje odpověď a extrahuje strukturované metriky
4. Výsledky se uloží do BigQuery a zobrazí v dashboardu

**Výstup na jeden dotaz:**
- **Je vaše značka viditelná?** — Byla zmíněna v odpovědi LLM?
- **Sentiment** — Pozitivní / Neutrální / Negativní / Žádný
- **Konkurence** — Které další značky se v odpovědi objevily?
- **Kontext** — Jednověté vysvětlení, jak byla značka zmíněna
- **Visibility Score** — Podíl zmínek značky (1 / N zmíněných konkurentů)

---

## Obchodní logika

### Proč na tom záleží

Tradiční SEO sleduje pozici ve výsledcích vyhledávačů. AI Visibility sleduje pozici v *odpovědích generovaných AI* — stále důležitější kanál, protože uživatelé přecházejí na konverzační AI pro průzkum produktů a nákupní rozhodování.

### Typy promptů

Dotazy se rozdělují do čtyř kategorií záměru:

| Typ | Příklad | Využití |
|---|---|---|
| **Informační** | „Jaké jsou nejlepší značky EV?" | Povědomí o kategorii |
| **Komerční** | „Kde koupit elektromobil?" | Nákupní záměr |
| **Konkurenční** | „Tesla vs Rivian vs Lucid?" | Poziční srovnání |
| **Navigační** | „Najdi web Tesly" | Hledání konkrétní značky |

### Klíčové metriky

- **Míra viditelnosti** — % dotazů, kde se značka objevila (napříč sezeními / v čase)
- **Rozložení sentimentu** — Podíl pozitivních / negativních / neutrálních zmínek
- **Unikátní konkurenti** — Konkurenční krajina v odpovědích LLM
- **Trendová analýza** — Viditelnost a sentiment v čase (denně / týdně / měsíčně)

### Testovací režim vs. ukládací režim

- **Test Run** — Dotaz se provede a výsledky se zobrazí pouze v aktuální session; nic se neukládá
- **Run & Save to BigQuery** — Stejný dotaz, ale výsledky se trvale uloží pro trendovou analýzu

---

## Technická architektura

```
┌─────────────────────────────────────────────┐
│        Streamlit Web App (app.py)            │
│                                             │
│  ┌──────────┐ ┌───────────┐ ┌───────────┐  │
│  │New Query │ │ Dashboard │ │  History  │  │
│  └──────────┘ └───────────┘ └───────────┘  │
└──────────────────┬──────────────────────────┘
                   │ HTTP POST (webhook)
                   ▼
         ┌──────────────────────┐
         │   n8n Workflow       │
         │  (automatizační vrstva)│
         └──────────┬───────────┘
                    │ REST API volání
                    ▼
         ┌──────────────────────┐
         │  Google Gemini API   │
         │  (dvouprůchodový vzor)│
         └──────────────────────┘

┌──────────────────────┐     ┌──────────────────────┐
│   Google BigQuery    │     │   Google OAuth2      │
│   (datový sklad)     │     │   (autentizace)      │
└──────────────────────┘     └──────────────────────┘
```

### Technologický stack

| Vrstva | Technologie |
|---|---|
| Frontend | Streamlit |
| Orchestrace workflow | n8n |
| LLM | Google Gemini API (Gemma-3-4B-IT) |
| Databáze | Google BigQuery |
| Autentizace | Google OAuth2 (Authlib) |
| Zpracování dat | pandas |

---

## Struktura aplikace

```
ai_visibility_tracker/
├── app.py                  # Hlavní Streamlit aplikace
├── bigquery_backend.py     # Čtení a zápis do BigQuery
├── config.json             # API klíče, webhook URL, BQ konfigurace (není v repozitáři)
├── service_account.json    # GCP service account přihlašovací údaje (není v repozitáři)
├── requirements.txt        # Python závislosti
├── n8n.json                # Export n8n workflow (není v repozitáři)
└── .streamlit/
    └── secrets.toml        # Generováno automaticky z config.json pro Streamlit auth
```

### `app.py` — Tříkartové UI

**Karta 1: New Query**
- Vstupní pole: název značky, URL značky, popis značky, text promptu, typ promptu
- Dvě akční tlačítka: Test Run / Run & Save to BigQuery
- Výsledky se zobrazí přímo po spuštění

**Karta 2: Dashboard**
- Souhrnné KPI: Celkem spuštění, Míra viditelnosti, Dominantní sentiment, Unikátní konkurenti
- Tabulka výsledků (sestupně dle času)
- Detail výsledku se surovou odpovědí LLM a kompletním JSON payloadem

**Karta 3: History**
- Výběr značky (načítá z BigQuery)
- Výběr granularity: Denně / Týdně / Měsíčně
- Spojnicový graf míry viditelnosti
- Sloupcový graf rozložení sentimentu
- Export surových dat jako tabulka

### `bigquery_backend.py` — Datová vrstva

Schéma tří tabulek:

| Tabulka | Účel |
|---|---|
| `brands` | Unikátní profily značek (název, URL, popis) |
| `prompts` | Unikátní texty promptů a jejich typy |
| `runs` | Jednotlivé výsledky dotazů propojující značky a prompty |

Klíčové operace:
- `save_run()` — Upsertuje značku a prompt, vloží záznam běhu
- `get_brands()` — Vrátí seřazený seznam sledovaných značek
- `get_visibility_history()` — Agregovaný časový dotaz pro kartu History

### n8n — Automatizační vrstva

Instance n8n běžící na VPS přijímá webhook volání ze Streamlitu a orchestruje dvě sekvenční volání Gemini API:

1. **Generování odpovědi** — Pošle LLM uživatelský dotaz a získá přirozenou, nestrannou odpověď
2. **Analýza odpovědi** — Předá tuto odpověď druhému LLM volání pro extrakci strukturovaného JSON: `is_visible`, `sentiment`, `context`, `competitors`
<img width="810" height="276" alt="image" src="https://github.com/user-attachments/assets/31915de2-8413-4d07-8519-807de8bf963d" />


Zpracovaný výsledek se vrátí synchronně do Streamlitu jako jeden JSON payload.

---

## Instalace a spuštění

### Předpoklady

- Python 3.10+
- Google Cloud projekt s povoleným BigQuery
- GCP service account s rolí BigQuery Data Editor
- Google OAuth2 přihlašovací údaje (pro autentizaci)
- Instance n8n s nasazeným a spuštěným workflow

