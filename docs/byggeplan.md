# Byggeplan — Project Red

## Hva er Project Red?

Project Red er en AI-agent som kobler seg til din eksisterende dbt-pipeline, diagnostiserer feil, tester fix i et syntetisk miljø, og åpner en GitHub PR med verifisert løsning.

**Project Red prosesserer ikke data. Den fikser kode.**

dbt + DuckDB brukes utelukkende som sandbox for å verifisere at en fix faktisk fungerer — ikke for å prosessere produksjonsdata.

---

## To versjoner

### Project Red (Open Source)
- For individuelle data engineers og små team
- pip-installert lokalt eller i CI
- Sandbox kjører på GitHub Actions hosted runner
- LLM via Anthropic, OpenAI API, eller Ollama (lokale modeller)
- Git via GitHub
- Designet med tydelige abstraksjonslag — enterprise-varianter plugges inn uten å røre kjernelogikken

### Project Red Enterprise
- Self-hosted av bedriften
- Identisk kodebase, annen konfig
- Sandbox kjører på intern CI-runner (self-hosted GHA, Azure DevOps, Jenkins)
- LLM via Azure OpenAI, Bedrock, eller Ollama
- Git via GitHub Enterprise, GitLab, Bitbucket
- Ingen data forlater nettverket

---

## Arkitektur

```
[Brukerens dbt pipeline feiler]
            ↓
[project-red agent]  ← pip-installert
  leser dbt logs + manifest.json
            ↓
[llm_client.py]           ← provider-pattern: Anthropic | OpenAI | Ollama
  diagnostiserer feil
  foreslår fix
            ↓
[sandbox_client.py]       ← provider-pattern: GHA | self-hosted (enterprise)
  trigger CI runner
            ↓
[Runner]
  DuckDB + dbt installert
  genererer syntetisk data fra schema
  tester fix — maks 5 forsøk
            ↓
  Grønn → git_handler.py åpner PR
  5 røde → varsling til utvikler
            ↓
[git_handler.py]          ← provider-pattern: GitHub | GHE | GitLab (enterprise)
```

---

## Filstruktur

```
project-red/
  agent.py              # koordinator — starter og styrer agent-loop
  llm_client.py         # LLM abstraksjon (Anthropic, OpenAI, Ollama)
  sandbox_client.py     # sandbox abstraksjon (GHA, self-hosted)
  git_handler.py        # git abstraksjon (GitHub, GHE, GitLab)
  config.yml            # brukerens konfig
  .github/
    workflows/
      sandbox.yml       # GHA sandbox workflow (open source versjon)
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

`sandbox_client.py` og `git_handler.py` følger samme prinsipp. Enterprise-varianter implementerer interfacet og konfigureres via `config.yml` — ingen endringer i `agent.py`.

---

## Konfigurasjon

### Open Source — Anthropic
```yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-haiku-4-5

sandbox:
  runner: github-actions

git:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: org/dbt-repo

dbt:
  project_path: ./dbt-project

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
  model: llama3.1:8b   # eller qwen3:8b, osv.
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
  project_path: ./dbt-project
```

---

## Byggefaser

### Fase 1 — agent.py + llm_client.py

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

`llm_client.py` instansieres basert på `config.yml`:
```python
def get_llm_client(config) -> LLMClient:
    provider = config["llm"]["provider"]
    if provider == "anthropic": return AnthropicClient(config)
    if provider == "openai":    return OpenAIClient(config)
    if provider == "ollama":    return OllamaClient(config)
    raise ValueError(f"Ukjent LLM provider: {provider}")
```

**Verifisering:** Introduser en kjent dbt-feil. Bekreft at agenten returnerer et fornuftig fix-forslag — test med minst én sky-provider og Ollama.

---

### Fase 2 — sandbox_client.py

**Mål:** Fix testes i isolert DuckDB + dbt miljø.

**Open Source:** `sandbox_client.py` trigger GHA workflow via GitHub API.

`sandbox.yml` (GHA workflow) gjør:
1. Installer dbt-core + dbt-duckdb
2. Generer syntetisk data basert på schema fra manifest
3. Appliser foreslått fix
4. Kjør `dbt run` + `dbt test`
5. Returner resultat til agenten

`sandbox_client.py` følger provider-pattern:
```python
def get_sandbox_client(config) -> SandboxClient:
    runner = config["sandbox"]["runner"]
    if runner == "github-actions": return GHAClient(config)
    if runner == "self-hosted":    return SelfHostedClient(config)  # enterprise stub
    raise ValueError(f"Ukjent sandbox runner: {runner}")
```

Sandbox-loop (maks 5 forsøk):
- Grønn → gå videre til fase 3
- Rød → send ny feilmelding tilbake til agenten, forsøk N+1
- 5 røde → gå til varsling uten PR

**Verifisering:** Introduser en kjent dbt-feil. Bekreft at sandboxen reproduserer feilen og at agenten itererer mot grønn.

---

### Fase 3 — git_handler.py

**Mål:** Agenten committer verifisert fix og åpner PR.

Steg:
1. Lag branch: `fix/dbt-{timestamp}`
2. Skriv fix
3. Push branch
4. Åpne PR med beskrivelse:
   - Hva feilet
   - Hva agenten undersøkte
   - Hvilke fixes ble forsøkt
   - Hva som til slutt fungerte

`git_handler.py` følger provider-pattern:
```python
def get_git_handler(config) -> GitHandler:
    provider = config["git"]["provider"]
    if provider == "github":            return GitHubHandler(config)
    if provider == "github-enterprise": return GHEHandler(config)    # enterprise stub
    if provider == "gitlab":            return GitLabHandler(config)  # enterprise stub
    raise ValueError(f"Ukjent git provider: {provider}")
```

**Verifisering:** Bekreft at PR er åpen med korrekt beskrivelse etter grønn sandbox.

---

### Fase 4 — Varsling

**Mål:** Agenten varsler utvikler uansett utfall.

- **Grønn:** "PR åpnet: fix/dbt-{timestamp}"
- **5 røde:** "Agenten klarte ikke å fikse feilen. Manuell handling kreves."

Kanal konfigureres i `config.yml`:
```yaml
notify:
  channel: email        # eller slack, teams
  to: dev@bedrift.no
```

---

### Fase 5 — Integrasjonstest

**Mål:** Ende-til-ende test av hele flyten.

Scenario:
1. Fungerende dbt-pipeline
2. Introduser kjent feil manuelt
3. Kjør `project-red watch --repo ./dbt-project`
4. Bekreft PR åpnes med korrekt fix

Test med minst to LLM-providers (f.eks. Anthropic + Ollama).

---

### Fase 6 — Distribusjon

```bash
pip install project-red
# + kopier .github/workflows/sandbox.yml inn i dbt-repoet
```

PyPI-publisering gjøres etter vellykket integrasjonstest.

---

## Oppsummering

| Fase | Hva | Output |
|------|-----|--------|
| 1 | agent.py + llm_client.py | Fix-forslag fra LLM |
| 2 | sandbox_client.py | Verifisert fix |
| 3 | git_handler.py | PR åpnet |
| 4 | Varsling | Utvikler informert |
| 5 | Integrasjonstest | Ende-til-ende grønn |
| 6 | Distribusjon | pip install project-red |