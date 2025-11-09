# automation/news_agent.py

import os, json, re, inspect
from datetime import datetime, timedelta
from urllib.parse import urlparse

from dotenv import load_dotenv
import requests
from github import Github, Auth

from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search   # ✅ enable live tools

# ───────────────────────── Env ─────────────────────────
root_env = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(root_env):
    load_dotenv(dotenv_path=root_env)
else:
    load_dotenv()

XAI_API_KEY  = (os.getenv("XAI_API_KEY") or "").strip()
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()   # In Actions this is secrets.GITHUB_TOKEN
GITHUB_REPO  = (os.getenv("GITHUB_REPO") or "spazedd/github.io").strip()
if not XAI_API_KEY:
    raise SystemExit("Missing XAI_API_KEY")
if not GITHUB_TOKEN:
    raise SystemExit("Missing GITHUB_TOKEN (use Actions' provided GITHUB_TOKEN or set in .env)")
if "/" not in GITHUB_REPO:
    raise SystemExit(f"GITHUB_REPO must be 'owner/repo'. Current: {GITHUB_REPO!r}")

# ───────────────────────── Dates/Paths ─────────────────────────
today = datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
file_name = f"news-{today}.json"
path_in_repo = f"data/{file_name}"
commit_message = f"Daily News {today}"

# ───────────────────────── Model ─────────────────────────
# Use a tool-enabled model (fast is fine for daily runs)
MODEL = "grok-4-fast"

# ───────────────────────── Domains (expanded) ─────────────────────────
ALLOWED_DOMAINS = {
    # Core
    "reuters.com","wsj.com","bbc.com","bbc.co.uk","thehill.com","nypost.com",
    "nationalreview.com","apnews.com","bloomberg.com","ft.com","politico.com",
    "washingtonexaminer.com","foxnews.com","newsmax.com",
    # Big US outlets
    "cbsnews.com","abcnews.go.com","nbcnews.com","axios.com",
    "nytimes.com","theguardian.com","marketwatch.com","businessinsider.com",
    # Topical / tech / science
    "coindesk.com","defensenews.com","militarytimes.com","nature.com","sciencedaily.com",
    "statnews.com","techcrunch.com","arstechnica.com","wired.com","technologyreview.com",
    "venturebeat.com","ieee.org","openai.com","tabletmag.com","city-journal.org",
    "thefp.com","thefederalist.com","justthenews.com","al-monitor.com","quillette.com",
}
DISALLOWED = {"example.com","localhost","127.0.0.1"}

def host_ok(host: str) -> bool:
    return bool(host) and any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)

# ───────────────────────── YOUR prompt (flat array; tools wording preserved) ─────────────────────────
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
"""

# ───────────────────────── xAI Call WITH TOOLS ─────────────────────────
client = Client(api_key=XAI_API_KEY)
chat = client.chat.create(
    model=MODEL,
    tools=[ web_search(), x_search() ],   # ✅ this is what enables live web access
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

# Extract the JSON array from the response (strip fences if present)
if raw.startswith("```"):
    raw = raw.strip("`")
    raw = raw[raw.find("\n")+1:]
start, end = raw.find("["), raw.rfind("]")
json_text = raw[start:end+1] if start != -1 and end != -1 else raw

# ───────────────────────── Parse & Normalize to flat list ─────────────────────────
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

out = []
for item in data:
    if not isinstance(item, dict): 
        continue
    title   = clean_text(item.get("title", ""))
    details = clean_text(item.get("details") or item.get("summary") or "")
    source  = (item.get("source") or item.get("url") or "").strip()
    if not (title and details and source):
        continue

    # Enforce 3–4 sentences best-effort
    sentences = re.split(r"(?<=[.!?])\s+", details)
    if len(sentences) > 4:
        details = " ".join(sentences[:4])

    # Soft URL validation (no live GET; many sites block CI runners)
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

    out.append({"title": title, "details": details, "source": source})

# If too few validated, fall back to cleaned raw model items (always publish)
MIN_PUBLISH = 8
if len(out) < MIN_PUBLISH:
    print("⚠️ Low validated count; falling back to unvalidated model output.")
    fallback = []
    for item in data:
        if not isinstance(item, dict): 
            continue
        t = clean_text(item.get("title") or "")
        d = clean_text(item.get("details") or item.get("summary") or "")
        s = (item.get("source") or item.get("url") or "").strip()
        if t and d and s:
            # keep 3–4 sentences best-effort
            parts = re.split(r"(?<=[.!?])\s+", d)
            if len(parts) > 4:
                d = " ".join(parts[:4])
            fallback.append({"title": t, "details": d, "source": s})
    if fallback:
        out = fallback[:15]

# Cap to 20
out = out[:20]

if not out:
    raise SystemExit("No usable stories produced — try rerunning or widening sources in prompt.")

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
    print(f"✅ Created {path_in_repo} with {len(out)} stories (tools enabled)")
except Exception:
    existing = repo.get_contents(path_in_repo, ref="main")
    repo.update_file(existing.path, commit_message, content, existing.sha, branch="main")
    print(f"♻️ Updated {path_in_repo} with {len(out)} stories (tools enabled)")