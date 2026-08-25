#!/usr/bin/env python3
"""
Bot de vagas -> Telegram
Busca vagas recém-publicadas em 8 plataformas, deduplica e envia
as novas para o seu chat do Telegram.

Fontes: Gupy, LinkedIn, Vagas.com.br, InfoJobs, Catho, Programathor
e (opcionais, com chave gratuita) Adzuna e Jooble — agregadores que
cobrem Indeed e dezenas de outros sites.

Feito para rodar de hora em hora no GitHub Actions.
Segredos necessários (Settings > Secrets and variables > Actions):
  TELEGRAM_TOKEN   - token do bot (do @BotFather)
  TELEGRAM_CHAT_ID - seu chat id (do @userinfobot)
Opcionais:
  ADZUNA_APP_ID / ADZUNA_APP_KEY - chave gratuita em developer.adzuna.com
  JOOBLE_KEY                     - chave gratuita em jooble.org/api/about
"""

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = BASE / "seen.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
JOOBLE_KEY = os.environ.get("JOOBLE_KEY", "").strip()

BRT = timezone(timedelta(hours=-3))  # America/Sao_Paulo (sem DST desde 2019)
NOW = datetime.now(timezone.utc)

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------- utilidades

def log(msg: str) -> None:
    print(f"[{datetime.now(BRT):%H:%M:%S}] {msg}", flush=True)


def job_id(url: str) -> str:
    """Id estável a partir da URL (sem parâmetros de tracking)."""
    clean = url.split("?")[0].rstrip("/")
    return hashlib.sha1(clean.encode()).hexdigest()[:16]


def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_seen(seen: dict) -> None:
    # mantém só os 8000 mais recentes para o arquivo não crescer para sempre
    if len(seen) > 8000:
        ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
        seen = dict(ordered[:8000])
    SEEN_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def title_ok(title: str, source: str) -> bool:
    t = f" {title.lower()} "
    protected = any(
        keep.lower() in t for keep in CONFIG["nunca_excluir_se_tiver"]
    )
    if not protected:
        for bad in CONFIG["excluir_no_titulo"]:
            if bad.lower() in t:
                return False
    # fontes cuja busca é "solta" exigem termo de tecnologia no título
    if source in CONFIG.get("fontes_com_filtro_de_relevancia", []):
        if not any(term.lower() in t for term in CONFIG["relevancia_titulo"]):
            return False
    return True


def parse_dt(value: str):
    """Converte ISO-8601 (com ou sem Z) em datetime aware, ou None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get(url: str, **kw):
    return requests.get(url, headers=HEADERS_BROWSER, timeout=30, **kw)


def card_text_lines(anchor) -> list:
    """Linhas de texto do cartão (elemento pai) de um link de vaga."""
    parent = anchor
    for _ in range(4):
        if parent.parent is None:
            break
        parent = parent.parent
        text = parent.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 2:
            return lines[:8]
    return []


# ------------------------------------------------------------------- fontes

def fetch_gupy(keyword: str) -> list:
    """API pública do portal de vagas da Gupy."""
    url = (
        "https://employability-portal.gupy.io/api/v1/jobs"
        f"?jobName={quote(keyword)}&limit=20&offset=0"
    )
    try:
        r = get(url)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as exc:  # noqa: BLE001
        log(f"  Gupy erro ({keyword}): {exc}")
        return []

    jobs = []
    for j in data:
        link = j.get("jobUrl") or ""
        if not link:
            continue
        city = (j.get("city") or "").strip()
        state = (j.get("state") or "").strip()
        wp = j.get("workplaceType") or ""
        if wp == "remote":
            place = "Remoto"
        else:
            place = ", ".join(x for x in (city, state) if x) or "Brasil"
            if wp == "hybrid":
                place += " (híbrido)"
        jobs.append({
            "title": j.get("name") or "(sem título)",
            "company": j.get("careerPageName") or "",
            "location": place,
            "url": link,
            "published": parse_dt(j.get("publishedDate")),
            "source": "Gupy",
            "remote": wp == "remote",
        })
    return jobs


def fetch_linkedin(keyword: str, location: str, seconds: int) -> list:
    """Endpoint público (guest) da busca de vagas do LinkedIn."""
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords={quote(keyword)}&location={quote(location)}"
        f"&f_TPR=r{seconds}&sortBy=DD&start=0"
    )
    try:
        r = get(url)
        if r.status_code == 429:
            log("  LinkedIn: rate limit (429), pulando esta busca")
            return []
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log(f"  LinkedIn erro ({keyword}): {exc}")
        return []

    jobs = []
    for card in soup.select("div.base-card, div.base-search-card"):
        a = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        t = card.select_one("h3.base-search-card__title")
        if not a or not t:
            continue
        comp = card.select_one("h4.base-search-card__subtitle")
        loc = card.select_one("span.job-search-card__location")
        when = card.select_one("time[datetime]")
        published = None
        if when and when.get("datetime"):
            try:
                published = datetime.strptime(
                    when["datetime"], "%Y-%m-%d"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        jobs.append({
            "title": t.get_text(strip=True),
            "company": comp.get_text(strip=True) if comp else "",
            "location": loc.get_text(strip=True) if loc else "",
            "url": a["href"].split("?")[0],
            "published": published,
            "source": "LinkedIn",
            "remote": False,
        })
    return jobs


def fetch_vagas_com(slug: str) -> list:
    """Vagas.com.br - página de busca ordenada por mais recentes."""
    url = (
        f"https://www.vagas.com.br/vagas-de-{slug}"
        "?ordenar_por=mais_recentes"
    )
    try:
        r = get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log(f"  Vagas.com erro ({slug}): {exc}")
        return []

    jobs, seen_here = [], set()
    for a in soup.select('a[href*="/vagas/v"]'):
        href = a.get("href", "")
        m = re.search(r"/vagas/v\d+/", href)
        title = a.get_text(strip=True)
        if not m or not title or href in seen_here:
            continue
        seen_here.add(href)
        if href.startswith("/"):
            href = "https://www.vagas.com.br" + href
        company, location = "", ""
        lines = card_text_lines(a)
        for ln in lines:
            low = ln.lower()
            if ln == title or "publicada" in low or "candidatura" in low:
                continue
            if not company:
                company = ln
            elif not location and re.search(r"[A-Za-z].*(,|\b[A-Z]{2}\b)", ln):
                location = ln
                break
        jobs.append({
            "title": title, "company": company, "location": location,
            "url": href.split("?")[0], "published": None,
            "source": "Vagas.com", "remote": "remoto" in title.lower(),
        })
    return jobs


def fetch_infojobs(keyword: str) -> list:
    """InfoJobs BR - página de busca por palavra-chave."""
    url = (
        "https://www.infojobs.com.br/empregos.aspx"
        f"?palabra={quote(keyword)}"
    )
    try:
        r = get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log(f"  InfoJobs erro ({keyword}): {exc}")
        return []

    jobs, seen_here = [], set()
    for a in soup.select('a[href*="vaga-de-"]'):
        href = a.get("href", "")
        if not re.search(r"__\d+\.aspx", href):
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4 or href in seen_here:
            continue
        seen_here.add(href)
        if href.startswith("/"):
            href = "https://www.infojobs.com.br" + href
        company, location = "", ""
        for ln in card_text_lines(a):
            low = ln.lower()
            if ln == title or "r$" in low or "salário" in low:
                continue
            if re.search(r" - [A-Z]{2}$", ln) or re.search(r", [A-Z]{2}\b", ln):
                location = ln
            elif not company and len(ln) < 60:
                company = ln
        jobs.append({
            "title": title, "company": company, "location": location,
            "url": href.split("?")[0], "published": None,
            "source": "InfoJobs", "remote": "home office" in title.lower(),
        })
    return jobs


def fetch_catho(slug: str) -> list:
    """Catho - página de busca por cargo."""
    url = f"https://www.catho.com.br/vagas/{slug}/"
    try:
        r = get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log(f"  Catho erro ({slug}): {exc}")
        return []

    jobs, seen_here = [], set()
    for a in soup.select('a[href*="catho.com.br/vagas/"], a[href^="/vagas/"]'):
        href = a.get("href", "")
        if not re.search(r"/vagas/[^/]+/\d+", href):
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4 or href in seen_here:
            continue
        seen_here.add(href)
        if href.startswith("/"):
            href = "https://www.catho.com.br" + href
        company, location = "", ""
        for ln in card_text_lines(a):
            low = ln.lower()
            if ln == title or "r$" in low or "combinar" in low:
                continue
            if not company and len(ln) < 60:
                company = ln
            elif not location and len(ln) < 60:
                location = ln
                break
        jobs.append({
            "title": title, "company": company, "location": location,
            "url": href.split("?")[0], "published": None,
            "source": "Catho", "remote": "remoto" in title.lower(),
        })
    return jobs


def fetch_programathor(path: str) -> list:
    """Programathor - vagas de tecnologia (filtros júnior/estágio)."""
    url = f"https://programathor.com.br{path}"
    try:
        r = get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log(f"  Programathor erro ({path}): {exc}")
        return []

    jobs, seen_here = [], set()
    for a in soup.select('a[href*="/jobs/"]'):
        href = a.get("href", "")
        m = re.search(r"/jobs/\d+-", href)
        if not m or href in seen_here:
            continue
        seen_here.add(href)
        text_lines = [
            ln.strip() for ln in a.get_text("\n", strip=True).split("\n")
            if ln.strip()
        ] or card_text_lines(a)
        if not text_lines:
            continue
        title = text_lines[0]
        company = text_lines[1] if len(text_lines) > 1 else ""
        location = next(
            (ln for ln in text_lines[2:5]
             if "remoto" in ln.lower() or re.search(r"- [A-Z]{2}$", ln)),
            "",
        )
        if href.startswith("/"):
            href = "https://programathor.com.br" + href
        jobs.append({
            "title": title, "company": company, "location": location,
            "url": href.split("?")[0], "published": None,
            "source": "Programathor",
            "remote": "remoto" in (title + location).lower(),
        })
    return jobs


def fetch_adzuna(keyword: str, hours: int) -> list:
    """Adzuna (agregador: cobre Indeed e dezenas de sites). Requer chave."""
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return []
    days = max(1, round(hours / 24))
    url = (
        "https://api.adzuna.com/v1/api/jobs/br/search/1"
        f"?app_id={ADZUNA_APP_ID}&app_key={ADZUNA_APP_KEY}"
        f"&what={quote(keyword)}&where={quote('São Paulo')}"
        f"&max_days_old={days}&sort_by=date&results_per_page=20"
        "&content-type=application/json"
    )
    try:
        r = get(url)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as exc:  # noqa: BLE001
        log(f"  Adzuna erro ({keyword}): {exc}")
        return []

    jobs = []
    for j in results:
        link = j.get("redirect_url") or ""
        if not link:
            continue
        jobs.append({
            "title": j.get("title", "").replace("<strong>", "")
                      .replace("</strong>", "") or "(sem título)",
            "company": (j.get("company") or {}).get("display_name", ""),
            "location": (j.get("location") or {}).get("display_name", ""),
            "url": link,
            "published": parse_dt(j.get("created")),
            "source": "Adzuna",
            "remote": False,
        })
    return jobs


def fetch_jooble(keyword: str) -> list:
    """Jooble (agregador). Requer chave gratuita."""
    if not JOOBLE_KEY:
        return []
    try:
        r = requests.post(
            f"https://br.jooble.org/api/{JOOBLE_KEY}",
            json={"keywords": keyword, "location": "São Paulo", "page": 1},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json().get("jobs", [])
    except Exception as exc:  # noqa: BLE001
        log(f"  Jooble erro ({keyword}): {exc}")
        return []

    jobs = []
    for j in results[:20]:
        link = j.get("link") or ""
        if not link:
            continue
        jobs.append({
            "title": re.sub(r"<[^>]+>", "", j.get("title", "")) or "(sem título)",
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "url": link,
            "published": parse_dt(j.get("updated", "")[:19] + "+00:00"
                                  if j.get("updated") else ""),
            "source": "Jooble",
            "remote": False,
        })
    return jobs


# ----------------------------------------------------------------- telegram

def tg(method: str, payload: dict) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data


def discover_chat_id() -> None:
    """Sem TELEGRAM_CHAT_ID: tenta descobrir via getUpdates e orienta."""
    log("TELEGRAM_CHAT_ID não configurado. Procurando via getUpdates...")
    try:
        data = tg("getUpdates", {})
        ids = {
            str(u["message"]["chat"]["id"])
            for u in data.get("result", [])
            if "message" in u
        }
        if ids:
            log(f">>> chat_id(s) encontrados: {', '.join(ids)}")
            log(">>> Adicione o secret TELEGRAM_CHAT_ID com esse valor.")
        else:
            log(">>> Nenhuma mensagem encontrada. Envie um 'oi' para o seu "
                "bot no Telegram e rode de novo, ou pegue seu id no "
                "@userinfobot.")
    except Exception as exc:  # noqa: BLE001
        log(f"getUpdates falhou: {exc}")
    sys.exit(1)


def send_job(job: dict) -> None:
    when = ""
    if job["published"]:
        when = f"\n🕐 {job['published'].astimezone(BRT):%d/%m %H:%M}"
    text = (
        f"🆕 <b>{html.escape(job['title'])}</b>\n"
        f"🏢 {html.escape(job['company'] or '—')}\n"
        f"📍 {html.escape(job['location'] or '—')}"
        f"{when}\n"
        f"🔎 {job['source']}\n\n"
        f"👉 <a href=\"{html.escape(job['url'])}\">Candidatar-se agora</a>"
    )
    tg("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


# --------------------------------------------------------------------- main

def collect_all(window_h: int) -> dict:
    """Roda todas as fontes e devolve {job_id: job} (sem filtrar seen)."""
    collected = {}

    loc_ok = re.compile(
        r"são paulo|\bsp\b|remoto|home office|híbrido", re.IGNORECASE)

    def add(found):
        for job in found:
            jid = job_id(job["url"])
            if jid in collected:
                continue
            if not title_ok(job["title"], job["source"]):
                continue
            # filtro de localização para TODAS as fontes:
            # aceita remoto, ou SP; localização vazia só passa se a fonte
            # já busca por região (LinkedIn/Adzuna/Jooble usam São Paulo)
            if job.get("remote"):
                pass
            elif job["location"].strip():
                if not loc_ok.search(job["location"]):
                    continue
            elif job["source"] in ("Gupy", "Vagas.com", "Catho"):
                continue  # sem localização em fonte nacional: descarta
            collected[jid] = job

    for kw in CONFIG["buscas"]:
        log(f"Buscando: {kw}")
        add(fetch_gupy(kw))
        add(fetch_linkedin(kw, CONFIG["localizacao_linkedin"],
                           window_h * 3600))
        add(fetch_adzuna(kw, window_h))
        add(fetch_jooble(kw))
        time.sleep(2)  # gentileza com as APIs

    # Fontes sem data de publicação: fora do horário comercial elas só
    # "giram" vagas antigas na primeira página. Só consulta das 07h às
    # 20h de Brasília (10h-23h UTC) para não virar ruído de madrugada.
    hora_utc = NOW.hour
    if 10 <= hora_utc <= 23:
        for slug in CONFIG.get("buscas_vagas_com", []):
            log(f"Buscando (Vagas.com): {slug}")
            add(fetch_vagas_com(slug))
            time.sleep(1)

        for kw in CONFIG.get("buscas_infojobs", []):
            log(f"Buscando (InfoJobs): {kw}")
            add(fetch_infojobs(kw))
            time.sleep(1)

        for slug in CONFIG.get("buscas_catho", []):
            log(f"Buscando (Catho): {slug}")
            add(fetch_catho(slug))
            time.sleep(1)

        for path in CONFIG.get("buscas_programathor", []):
            log(f"Buscando (Programathor): {path}")
            add(fetch_programathor(path))
            time.sleep(1)
    else:
        log("(Fontes sem data pausadas fora do horário 07h-20h BRT)")

    return collected


def main() -> None:
    if not TELEGRAM_TOKEN:
        log("ERRO: defina o secret TELEGRAM_TOKEN.")
        sys.exit(1)
    if not TELEGRAM_CHAT_ID:
        discover_chat_id()

    seen = load_seen()
    first_run = not seen
    window_h = (
        CONFIG["janela_horas_primeira_execucao"]
        if first_run else CONFIG["janela_horas"]
    )
    cutoff = NOW - timedelta(hours=window_h)
    log(f"Janela: últimas {window_h}h | já vistas: {len(seen)}")
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        log("(Adzuna desativado - sem chave)")
    if not JOOBLE_KEY:
        log("(Jooble desativado - sem chave)")

    collected = collect_all(window_h)
    fresh = {
        jid: job for jid, job in collected.items()
        if jid not in seen
        and not (job["published"] and job["published"] < cutoff)
    }

    jobs = sorted(
        fresh.values(),
        key=lambda j: j["published"] or datetime.min.replace(
            tzinfo=timezone.utc),
        reverse=True,
    )[: CONFIG["max_vagas_por_execucao"]]

    por_fonte = {}
    for j in fresh.values():
        por_fonte[j["source"]] = por_fonte.get(j["source"], 0) + 1
    log(f"Novas: {len(fresh)} {por_fonte} | enviando até {len(jobs)}")

    sent = 0
    enviadas_agora = []
    for job in jobs:
        try:
            send_job(job)
            seen[job_id(job["url"])] = NOW.isoformat()
            enviadas_agora.append({
                "t": NOW.isoformat(),
                "title": job["title"], "company": job["company"],
                "location": job["location"], "source": job["source"],
                "url": job["url"],
            })
            sent += 1
            time.sleep(1.2)  # limite do Telegram: ~1 msg/s
        except Exception as exc:  # noqa: BLE001
            log(f"Falha ao enviar '{job['title']}': {exc}")

    if enviadas_agora:
        env_file = BASE / "enviadas.json"
        try:
            historico = json.loads(env_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            historico = []
        historico.extend(enviadas_agora)
        env_file.write_text(
            json.dumps(historico[-400:], ensure_ascii=False, indent=0),
            encoding="utf-8")

    if first_run:
        # não deixa o excedente da primeira rodada virar spam nas próximas
        for jid in collected:
            seen.setdefault(jid, NOW.isoformat())

    save_seen(seen)
    log(f"Concluído: {sent} vaga(s) enviada(s).")

    if first_run:
        try:
            tg("sendMessage", {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (f"✅ Bot de vagas no ar! Fontes ativas: Gupy, "
                         f"LinkedIn, Vagas.com, InfoJobs, Catho e "
                         f"Programathor"
                         + (", Adzuna" if ADZUNA_APP_ID else "")
                         + (", Jooble" if JOOBLE_KEY else "")
                         + f". Nesta primeira rodada enviei {sent} vaga(s); "
                         "a partir de agora, só novidades, de hora em hora."),
            })
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
