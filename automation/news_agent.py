# automation/news_agent.py

import os, json, re, inspect
from datetime import datetime, timedelta
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests
from github import Github, Auth

from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search   # ✅ live tools

# ───────────────────────── Env ─────────────────────────
root_env = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(root_env):
    load_dotenv(dotenv_path=root_env)
else:
    load_dotenv()

XAI_API_KEY  = (os.getenv("XAI_API_KEY") or "").strip()
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
GITHUB_REPO  = (os.getenv("GITHUB_REPO") or "spazedd/github.io").strip()
if not XAI_API_KEY:
    raise SystemExit("Missing XAI_API_KEY")
if not GITHUB_TOKEN:
    raise SystemExit("Missing GITHUB_TOKEN (use Actions' GITHUB_TOKEN or set in .env)")
if "/" not in GITHUB_REPO:
    raise SystemExit(f"GITHUB_REPO must be 'owner/repo'. Current: {GITHUB_REPO!r}")

# ───────────────────────── Dates/Paths ─────────────────────────
today = datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
file_name = f"news-{today}.json"
path_in_repo = f"data/{file_name}"
commit_message = f"Daily News {today}"

# ───────────────────────── Model ─────────────────────────
MODEL = "grok-4-fast"  # tool-enabled

# ───────────────────────── Domains ─────────────────────────
ALLOWED_DOMAINS = {
    # Core/broad
    "reuters.com","wsj.com","bbc.com","bbc.co.uk","thehill.com","nypost.com",
    "nationalreview.com","apnews.com","bloomberg.com","ft.com","politico.com",
    "washingtonexaminer.com","foxnews.com","newsmax.com",
    # Big US outlets
    "cbsnews.com","abcnews.go.com","nbcnews.com","axios.com",
    "nytimes.com","theguardian.com","marketwatch.com","businessinsider.com",
    # Tech/science/health
    "techcrunch.com","arstechnica.com","wired.com","technologyreview.com","venturebeat.com",
    "ieee.org","openai.com","statnews.com","nature.com","sciencedaily.com",
    # Security/defense, culture
    "defensenews.com","militarytimes.com","al-monitor.com","city-journal.org","tabletmag.com",
    "thefp.com","thefederalist.com","justthenews.com","quillette.com","coindesk.com",
}
DISALLOWED = {"example.com","localhost","127.0.0.1"}

def host_ok(host: str) -> bool:
    return bool(host) and any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)

def base_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"} and parts[-1] in {"uk","au"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()

def rebalance_by_domain(valid, raw_pool, target=20, per_cap=3):
    """Limit to per_cap per domain; then backfill from raw_pool to hit target with more domains."""
    picked, counts, used = [], {}, set()
    for it in valid:
        try:
            d = base_domain(urlparse(it["source"]).netloc)
        except Exception:
            continue
        if counts.get(d, 0) >= per_cap:
            continue
        picked.append(it)
        counts[d] = counts.get(d, 0) + 1
        used.add(it["source"])
    if len(picked) < target:
        for it in raw_pool:
            if not isinstance(it, dict): 
                continue
            s = (it.get("source") or it.get("url") or "").strip()
            t = (it.get("title") or "").strip()
            dsc = (it.get("details") or it.get("summary") or "").strip()
            if not (t and dsc and s) or s in used:
                continue
            try:
                host = urlparse(s).netloc
            except Exception:
                continue
            if not host or not host_ok(host):
                continue
            bd = base_domain(host)
            if counts.get(bd, 0) >= per_cap:
                continue
            parts = re.split(r"(?<=[.!?])\s+", dsc)
            if len(parts) > 4:
                dsc = " ".join(parts[:4])
            picked.append({"title": t, "details": dsc, "source": s})
            counts[bd] = counts.get(bd, 0) + 1
            used.add(s)
            if len(picked) >= target:
                break
    return picked[:target]

# ───────────────────────── Prompt (your style + 2 small diversity nudges) ─────────────────────────
prompt = f"""
Generate a flat JSON array (valid JSON, no markdown, no extra text) of 15-20 stories from TODAY ONLY ({today}).
Use categories (family, security, economy, health, tech, world) internally for variety, but DO NOT include them in output.
Each story: {{
  "title": "Concise, human title (<= 15 words; no 'Analysis:' or labels)",
  "details": "3–4 full sentences: Fact, context, effect, and a short human takeaway (America First angle, logical). 400–600 characters total.",
  "source": "https://real-article-url-from-today"
}}
Rules:
- **Must use tools for real data:** First, use web_search and x_keyword_search with since:{today} until:{tomorrow} to find recent stories. Then, use browse_page on promising URLs to extract and verify content published today. Ensure all stories are from credible outlets (Reuters, WSJ, BBC, The Hill, NY Post, National Review, etc.).
- Prioritize real, verified news; blend politics/econ/tech/world with quirky items. Exclude any fabricated or undated content.
- Align with America First: Praise sovereignty/efficiency; critique gov waste/foreign aid/feminist issues/cultural fads.
- Details flow: Fact, context (national trends), effect (econ/security/family), takeaway (unique, e.g., 'Puts citizens first—finally.').
- Use X conservative perspectives subtly from users like unusual_whales, Cernovich (don't name), but summarize real articles.
- Target variety: 2-4 per internal category, total 15-20.
- Ensure REAL URLs from today's publications; search exhaustively and verify with tools to avoid fakes.
- If tools return limited results, note in details but aim for quality over quantity.
- **Diversity requirement:** Include at least 6 distinct outlets overall; paywalled URLs are allowed.
- **Cap per outlet:** No more than 3 stories from any single outlet/domain.
"""

# ───────────────────────── xAI Call WITH TOOLS ─────────────────────────
client = Client(api_key=XAI_API_KEY)
chat = client.chat.create(
    model=MODEL,
    tools=[ web_search(), x_search() ],   # ✅ live web & X search
    messages=[]
)
chat.append(user(prompt))

def sample_chat(chat_obj, temperature=0.2, max_tokens=2500):
    sig = inspect.signature(chat_obj.sample)
    params = list(sig.parameters.values())
    names = {p.name for p in params}
    if any(p.kind == p.VAR_KEYWORD for p in params):
        return chat_obj.sample(temperature=temperature, max_tokens=max_tokens)
    kw = {}
    if "temperature" in names: kw["temperature"] = temperature
    if "max_tokens"  in names: kw["max_tokens"]  = max_tokens
    return chat_obj.sample(**kw) if kw else chat_obj.sample()

resp = sample_chat(chat)
raw = (getattr(resp, "content", None) or "").strip()

# Extract JSON array
if raw.startswith("```"):
    raw = raw.strip("`")
    raw = raw[raw.find("\n")+1:]
start, end = raw.find("["), raw.rfind("]")
json_text = raw[start:end+1] if start != -1 and end != -1 else raw

# ───────────────────────── Parse & Normalize ─────────────────────────
try:
    data = json.loads(json_text)
except json.JSONDecodeError as e:
    raise SystemExit(f"JSON decode error: {e}\nRaw head:\n{raw[:400]}")

if not isinstance(data, list):
    flat = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                flat.extend(v)
    data = flat

BAD_PREFIXES = r"(analysis|opinion|report|explainer|oddity|update|breaking|economic report|culture watch)"
def clean_text(s: str) -> str:
    s = str(s or "")
    s = re.sub(rf"^\s*{BAD_PREFIXES}\s*[:\-–]\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s

validated = []
for item in data:
    if not isinstance(item, dict): 
        continue
    title   = clean_text(item.get("title", ""))
    details = clean_text(item.get("details") or item.get("summary") or "")
    source  = (item.get("source") or item.get("url") or "").strip()
    if not (title and details and source):
        continue
    # 3–4 sentences (best effort)
    parts = re.split(r"(?<=[.!?])\s+", details)
    if len(parts) > 4:
        details = " ".join(parts[:4])
    # soft URL validation (domain + has path)
    try:
        p = urlparse(source)
    except Exception:
        continue
    if p.scheme not in {"http","https"}: 
        continue
    if not p.netloc or not p.path.strip("/"): 
        continue
    if p.netloc in DISALLOWED or not host_ok(p.netloc.lower()): 
        continue
    validated.append({"title": title, "details": details, "source": source})

# Fallback if needed (publish anyway)
MIN_PUBLISH = 8
out = validated
if len(out) < MIN_PUBLISH:
    print("⚠️ Low validated count; falling back to cleaned model output for backfill.")
    fallback = []
    for item in data:
        if not isinstance(item, dict): 
            continue
        t = clean_text(item.get("title") or "")
        d = clean_text(item.get("details") or item.get("summary") or "")
        s = (item.get("source") or item.get("url") or "").strip()
        if not (t and d and s):
            continue
        parts = re.split(r"(?<=[.!?])\s+", d)
        if len(parts) > 4:
            d = " ".join(parts[:4])
        fallback.append({"title": t, "details": d, "source": s})
    # merge keeping preferred validated first
    seen = {it["source"] for it in out}
    for it in fallback:
        if it["source"] not in seen:
            out.append(it)
            seen.add(it["source"])

# Rebalance: ≤3 per outlet, aim for 20 total & ≥6 outlets encouraged
TARGET = 20
PER_DOMAIN_CAP = 3
out = rebalance_by_domain(out, data, target=TARGET, per_cap=PER_DOMAIN_CAP)

if not out:
    raise SystemExit("No usable stories produced after rebalancing.")

# Cap to 20 just in case
out = out[:20]

# ───────────────────────── Save & Push ─────────────────────────
os.makedirs(os.path.join(os.path.dirname(__file__), "..", "data"), exist_ok=True)
local_path = os.path.join(os.path.dirname(__file__), "..", file_name)
with open(local_path, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

gh = Github(auth=Auth.Token(GITHUB_TOKEN))
repo = gh.get_repo(GITHUB_REPO)
with open(local_path, "r", encoding="utf-8") as f:
    content = f.read()

try:
    repo.create_file(path_in_repo, commit_message, content, branch="main")
    print(f"✅ Created {path_in_repo} with {len(out)} stories (tools + rebalance)")
except Exception:
    existing = repo.get_contents(path_in_repo, ref="main")
    repo.update_file(existing.path, commit_message, content, existing.sha, branch="main")
    print(f"♻️ Updated {path_in_repo} with {len(out)} stories (tools + rebalance)")
