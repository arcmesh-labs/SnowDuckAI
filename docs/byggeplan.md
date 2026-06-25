# Byggeplan — SnowDuckAI

## Hva er SnowDuckAI?

SnowDuckAI er en AI-agent som kobler seg til din eksisterende dbt-pipeline, diagnostiserer feil, tester fix i et syntetisk miljø, og åpner en GitHub PR med verifisert løsning.

**SnowDuckAI prosesserer ikke data. Den fikser kode.**

dbt + DuckDB brukes utelukkende som sandbox for å verifisere at en fix faktisk fungerer — ikke for å prosessere produksjonsdata.

---

## To versjoner

### SnowDuckAI (Open Source)
- For individuelle data engineers og små team
- pip-installert lokalt (`pip install snowduckai`)
- Sandbox kjører på GitHub Actions hosted runner
- LLM via Anthropic, OpenAI API, eller Ollama (lokale modeller)
- Git via GitHub
- Designet med tydelige abstraksjonslag — enterprise-varianter plugges inn uten å røre kjernelogikken

### SnowDuckAI Enterprise
- Self-hosted av bedriften
- Identisk kodebase, annen konfig
- Sandbox kjører på intern CI-runner (self-hosted GHA, Azure DevOps, Jenkins)
- LLM via Azure OpenAI, Bedrock, eller Ollama
- Git via GitHub Enterprise, GitLab, Bitbucket
- Ingen data forlater nettverket

---

## Brukerflyt

```bash
pip install snowduckai        # én gang
sd init                       # i dbt-prosjektmappen — lager config + sandbox.yml
# fyll inn config.yml med API-nøkler og GitHub token

dbt run                       # bruker kjører dbt som normalt
# feiler

sd debug --log logs/dbt.log   # starter agent-loopen manuelt
# → agent diagnostiserer, sandbox tester, PR åpnes
```

Fremtidig utvidelse:
```bash
sd watch                      # lytter til dbt.log og starter automatisk ved feil
```

---

## Arkitektur

```
[Brukerens dbt pipeline feiler]
            ↓
[sd debug]  ← pip-installert, kjører lokalt
  leser dbt logs + manifest.json
            ↓
[llm_client.py]           ← provider-pattern: Anthropic | OpenAI | Ollama
  diagnostiserer feil
  foreslår fix
            ↓
[sandbox_client.py]       ← provider-pattern: GHA | self-hosted (enterprise)
  trigger GHA via GitHub API
            ↓
[GHA Runner]
  DuckDB + dbt installert
  appliserer foreslått fix
  kjører dbt run + dbt test
  tester fix — maks 5 forsøk
            ↓
  Grønn → git_handler.py åpner PR
  5 røde → varsling til utvikler
            ↓
[git_handler.py]          ← provider-pattern: GitHub | GHE | GitLab (enterprise)
```

Brukerens maskin gjør kun:
- LLM-kall (Anthropic/OpenAI/Ollama)
- GitHub API-kall (trigger sandbox, åpne PR)

Alt det tunge kjører på GHA — ingen lokal DuckDB eller dbt nødvendig.

---

## Filstruktur

```
snowduckai/
  agent.py              # koordinator — starter og styrer agent-loop
  llm_client.py         # LLM abstraksjon (Anthropic, OpenAI, Ollama)
  sandbox_client.py     # sandbox abstraksjon (GHA, self-hosted)
  git_handler.py        # git abstraksjon (GitHub, GHE, GitLab)
  notifier.py           # varsling (email, Slack, Teams)
  cli.py                # sd / snowduckai CLI (init, debug, watch)
  .github/
    workflows/
      sandbox.yml       # GHA sandbox workflow (kopieres til brukerens repo)
```

### Provider-pattern

Hver abstraksjonslag følger samme mønster:

```python
class LLMClient:
    def __init__(self, config): ...
    def complete(self, messages, tools): ...

class AnthropicClient(LLMClient): ...
class OpenAIClient(LLMClient): ...
class OllamaClient(LLMClient): ...   # lokale modeller
```

`sandbox_client.py` og `git_handler.py` følger samme prinsipp. Enterprise-varianter implementerer interfacet og konfigureres via `snowduckai.yml` — ingen endringer i `agent.py`.

---

## Konfigurasjon

`sd init` lager `snowduckai.yml` med placeholder-verdier i dbt-prosjektmappen. Bruker fyller inn manuelt — se README.

### Open Source — Anthropic
```yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-haiku-4-5

sandbox:
  runner: github-actions
  token: ${GITHUB_TOKEN}
  repo: org/dbt-repo

git:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: org/dbt-repo

dbt:
  project_path: .

notify:
  channel: email
  to: dev@bedrift.no
```

### Open Source — OpenAI
```yaml
llm:
  provider: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o-mini
```

### Open Source — Ollama (lokale modeller)
```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434
  model: llama3.1:8b
```

### Enterprise
```yaml
llm:
  provider: azure-openai
  endpoint: https://intern.openai.azure.com
  api_key: ${AZURE_OPENAI_KEY}

sandbox:
  runner: self-hosted
  url: https://intern-ci.bedrift.no
  token: ${CI_TOKEN}

git:
  provider: github-enterprise
  url: https://git.bedrift.no
  token: ${GIT_TOKEN}
  repo: org/dbt-repo

dbt:
  project_path: .
```

---

## Byggefaser

### Fase 1 — agent.py + llm_client.py ✅

**Mål:** Agenten leser en dbt-feilmelding og returnerer et fix-forslag.

Input:
- `logs/dbt.log` — feilmeldingen
- `manifest.json` — dbt graph og schema
- `models/` — relevante SQL-modeller

`agent.py` starter en tool-use loop:
1. Send feilmelding + manifest som innledende prompt
2. LLM resonnerer og kaller tools fritt
3. Loop fortsetter til LLM returnerer fix-forslag
4. Fix-forslag sendes til sandbox

Tools (read-only):
- `read_file(path)` — les en fil fra dbt-prosjektet
- `list_directory(path)` — list filer i en mappe

---

### Fase 2 — sandbox_client.py ✅

**Mål:** Fix testes i isolert DuckDB + dbt miljø på GHA.

Sandbox-loop (maks 5 forsøk):
- Grønn → gå videre til fase 3
- Rød → send ny feilmelding tilbake til agenten, forsøk N+1
- 5 røde → gå til varsling uten PR

---

### Fase 3 — git_handler.py ✅

**Mål:** Agenten committer verifisert fix og åpner PR.

Steg:
1. Lag branch: `fix/dbt-{timestamp}`
2. Skriv fix
3. Push branch
4. Åpne PR med beskrivelse av hva som feilet, hva som ble undersøkt, og hvilken fix som fungerte

---

### Fase 4 — Varsling ✅

**Mål:** Agenten varsler utvikler uansett utfall.

- **Grønn:** "PR åpnet: fix/dbt-{timestamp}"
- **5 røde:** "Agenten klarte ikke å fikse feilen. Manuell handling kreves."

---

### Fase 5 — Integrasjonstest ✅

Ende-til-ende grønn: agent diagnostiserer → GHA sandbox → PR åpnet.

---

### Fase 6 — CLI (sd / snowduckai)

**Mål:** Brukervennlig CLI som pakker agent-loopen.

Kommandoer:
```bash
sd init       # lager snowduckai.yml + .github/workflows/sandbox.yml i dbt-prosjektet
sd debug      # starter agent-loopen: sd debug --log logs/dbt.log
sd watch      # (kommer senere) lytter til dbt.log, starter ved feil
```

`sd` og `snowduckai` er aliaser for samme CLI.

`sd init` lager kun filer — ingen interaktiv prompt. Bruker fyller inn config manuelt per README.

---

### Fase 7 — Distribusjon (PyPI)

```bash
pip install snowduckai
```

Krav før publisering:
- CLI fungerer end-to-end
- README er ferdig
- PyPI-pakkenavn `snowduckai` er tilgjengelig

---

## Oppsummering

| Fase | Hva | Status |
|------|-----|--------|
| 1 | agent.py + llm_client.py | ✅ |
| 2 | sandbox_client.py + GHA | ✅ |
| 3 | git_handler.py | ✅ |
| 4 | Varsling | ✅ |
| 5 | Integrasjonstest | ✅ |
| 6 | CLI (sd init, sd debug, sd watch) | — |
| 7 | PyPI (pip install snowduckai) | — |