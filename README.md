# Job Monitor v4 — Career page monitor + Telegram

Monitora le pagine careers di aziende e ti avvisa su Telegram quando escono
nuove offerte.

## Cosa è cambiato in v4 (rispetto a v3)

Il problema della v3 era che lo scraping HTML falliva su moltissimi siti
perché:
- gli HTML erano renderizzati lato client e i selettori CSS andavano fuori uso;
- diversi URL puntavano a pagine sbagliate o spostate (404);
- alcuni siti hanno banner cookies o paginazione che bloccavano l'estrazione.

**v4 cambia strategia:**

1. **API-first:** per le piattaforme ATS principali (Greenhouse, Lever, Ashby,
   Workday, Oracle JP Morgan, Microsoft, Amazon, Eightfold, SAP, Phenom-Booking)
   il monitor usa direttamente le **API JSON pubbliche**. È più veloce,
   stabile e non si rompe quando cambiano i selettori.
2. **Playwright migliorato:** per i siti che richiedono un browser (Google,
   Meta, Snowflake, Phenom in generale, ecc.) Playwright viene usato con
   gestione robusta dei cookie banner (oltre 30 selettori in italiano e
   inglese), scroll progressivo, e paginazione "click next" o infinite scroll.
3. **URL corretti** in `sites.json`. Sono stati riparati URL 404
   (OpenAI → Ashby, JetBrains → Greenhouse EU, Snowflake → Phenom careers,
   Microsoft, Optiver, Bending Spoons, Revolut, Samsung ...).
4. **Override del tipo:** se lo `sites.json` ha un campo `"type": "..."`,
   quello prevale sull'autodetect. Utile per forzare un handler.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Configura `settings.json` con il tuo bot Telegram (già configurato).

## Uso

```bash
# Avvio normale (loop ogni 30 min, manda notifiche su Telegram)
python job_monitor.py

# Test: un singolo giro, NESSUNA notifica, NON salva lo stato
python job_monitor.py --test

# Test su un singolo sito
python job_monitor.py --test --site=Anthropic
python job_monitor.py --test --site="Morgan Stanley"
```

Usa `--test` la prima volta per verificare che ogni sito restituisca > 0
offerte. È la stessa cosa della prima esecuzione "vera" tranne che non manda
notifiche e non salva lo stato.

## Aggiungere nuovi siti

Modifica `sites.json` aggiungendo:
```json
{ "name": "MyCompany", "url": "https://..." }
```
Opzionalmente, forza un handler: `"type": "greenhouse"` (oppure lever, ashby,
workday, eightfold, oracle, phenom, sap, playwright, ecc.).

Il monitor rilegge `sites.json` ad ogni ciclo: niente da riavviare.

## Risultati attesi

In `--test` ogni sito deve dare un conteggio > 0 al primo passaggio. I
conteggi sono limitati a ~200 per sito (paginazione).

Esempio output (testato dal sandbox: solo handler API, gli altri richiedono
chromium installato):

```
Google DeepMind     greenhouse  count=   66
Anthropic           greenhouse  count=  424
OpenAI              ashby       count=  661
Databricks          greenhouse  count=  812
JP Morgan           oracle      count=  200
Amazon              amazon      count=  200
Stripe              greenhouse  count=  494
JetBrains           greenhouse  count=  111
SAP                 sap         count=  188
BlackRock           workday     count=  195
Replit              ashby       count=   86
Prior Labs          ashby       count=   17
Booking.com         phenom_book count=   10
Mistral AI          lever       count=  162
LangChain           ashby       count=   93
Netflix             eightfold   count=   10  (paginates a >500)
```

I siti rimanenti (Google, Meta, Phenom, Goldman Sachs, Snowflake, BCG X,
Allianz, Barclays, ecc.) usano Playwright e devono essere testati sulla tua
macchina con `python job_monitor.py --test`.

## Risoluzione problemi

- **0 offerte su un sito API:** verifica l'URL in `sites.json`. Spesso le
  aziende migrano da una piattaforma all'altra (es. Greenhouse → Ashby). Cerca
  "<azienda> careers" e aggiorna l'URL.
- **0 offerte su un sito Playwright:** controlla il log. Quasi sempre il
  problema è un selettore CSS non più valido. Apri la pagina nel browser, fai
  ispeziona elemento sul titolo di un'offerta e aggiungi il selettore in
  `GENERIC_PROFILE["selectors"]` o nel profilo specifico (es. `handle_phenom`).
- **Sito blocca il bot (403):** il sito ha bot detection (es. Cloudflare). 
  Per il fix usa Playwright con `args=['--disable-blink-features=...']`
  (già attivo) e aggiungi delay più lunghi.

## File

| File | Cosa fa |
|---|---|
| `job_monitor.py` | Script principale (v4) |
| `sites.json` | Lista siti — modificabile a caldo |
| `settings.json` | Token e chat ID Telegram |
| `seen_jobs.json` | Stato (auto-generato) |
| `job_monitor.log` | Log di esecuzione |
| `requirements.txt` | Dipendenze Python |

## Aziende preconfigurate (38)

Google, Google DeepMind, Meta, Anthropic, OpenAI, Databricks, BCG X,
QuantumBlack (McKinsey), JP Morgan, Goldman Sachs, Optiver, Snowflake, Amazon,
Uber, Microsoft, Stripe, JetBrains, Generali, Allianz, UniCredit, SAP, UBS,
BlackRock, Morgan Stanley, Replit, Jane Street, Prior Labs, Revolut, Samsung,
Booking.com, Zalando, Bayer, Mistral AI, LangChain, Netflix, Barclays, Bain &
Company, Bending Spoons.
