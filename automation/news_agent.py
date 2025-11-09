# news_agent.py

import os, json, re, inspect, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import user
from github import Github, Auth

# --- ENV ---
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
XAI_API_KEY  = (os.getenv("XAI_API_KEY") or "").strip()
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
GITHUB_REPO  = (os.getenv("GITHUB_REPO") or "spazedd/github.io").strip()
if not XAI_API_KEY:  raise SystemExit("Missing XAI_API_KEY in .env or Actions secret")
if not GITHUB_TOKEN: raise SystemExit("Missing GITHUB_TOKEN in .env or Actions env")
if "/" not in GITHUB_REPO: raise SystemExit(f"GITHUB_REPO must be 'owner/repo'. Current: {GITHUB_REPO!r}")

# --- Dates / paths ---
today = datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
file_name = f"news-{today}.json"
path_in_repo = f"data/{file_name}"
commit_message = f"Daily News {today}"

# --- Model ---
MODEL = "grok-4"

# --- PROMPT: flat array; NO summary; details 3–4 sentences ---
prompt = f"""
Output a VALID JSON array (no markdown, no backticks, no text before/after) of 15–25 stories from TODAY ONLY ({today}).
Each story must be a single object with EXACTLY these keys:
{{
  "title": "Concise, human title (<= 15 words; no 'Analysis:' or labels)",
  "details": "3–4 full sentences: Fact, context, effect, and a short takeaway (human, varied). 400–600 characters total.",
  "source": "https://example.com/article-url"
}}
Rules:
- Use categories (family, economy, security, health, tech, world) only as internal guidance for variety; DO NOT include any categories, 'breaking', or extra fields in the JSON.
- Source from major outlets (Reuters, WSJ, BBC, The Hill, NY Post, The Spectator, National Review, Free Press, Federalist, etc.). Blend big issues + a few controversial items.
- Titles and details must sound natural and human; strictly avoid prefixes like 'Analysis:', 'Report:', 'Oddity:'.
- Strictly restrict to content published on {today}. Use since:{today} until:{tomorrow}. Exclude older/undated items.
"""

# --- xAI call ---
client = Client(api_key=XAI_API_KEY)
chat = client.chat.create(model=MODEL, messages=[])
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

def extract_json(s: str) -> str:
    if s.startswith("```"):
        s = s.strip("`"); s = s[s.find("\n")+1:]
    start, end = s.find("["), s.rfind("]")
    return s[start:end+1] if start != -1 and end != -1 else s

json_text = extract_json(raw)

# --- Load & normalize to required shape: [{title, details, source}, ...] ---
try:
    data = json.loads(json_text)
except json.JSONDecodeError as e:
    raise SystemExit(f"JSON decode error: {e}\nRaw head:\n{raw[:400]}")

# If a dict slipped through, flatten lists inside it
if not isinstance(data, list):
    flat = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list): flat.extend(v)
    data = flat

BAD_PREFIXES = r"(analysis|opinion|report|explainer|oddity|update|breaking|economic report|culture watch)"

def clean_text(s: str) -> str:
    if not isinstance(s, str): return s
    s = re.sub(rf"^\s*{BAD_PREFIXES}\s*[:\-–]\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Map/trim to exactly the keys we need; drop anything else; ensure 3–4 sentences in details (best-effort)
out = []
for item in data:
    if not isinstance(item, dict): continue
    title  = clean_text(item.get("title", ""))
    source = item.get("source") or item.get("url") or ""
    details = clean_text(item.get("details") or item.get("summary") or "")
    if not title or not details or not source:  # require all three
        continue

    # Basic sentence enforcement: split by period; keep 3–4 sentences if possible
    sentences = re.split(r"(?<=[.!?])\s+", details)
    if len(sentences) >= 5:
        details = " ".join(sentences[:4])  # cap at 4 sentences
    elif len(sentences) < 3:
        # leave as-is; model should mostly comply, but we don't fabricate
        details = details

    out.append({"title": title, "details": details, "source": source})

# --- Save local ---
os.makedirs("data", exist_ok=True)
with open(file_name, "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

# --- Push to GitHub ---
check = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}", timeout=10)
if check.status_code != 200:
    raise SystemExit(
        f"Repo not found publicly: {GITHUB_REPO} (status {check.status_code}). "
        "Ensure GITHUB_REPO=spazedd/github.io in .env."
    )

gh = Github(auth=Auth.Token(GITHUB_TOKEN))
repo = gh.get_repo(GITHUB_REPO)
with open(file_name, "r") as f:
    content = f.read()

try:
    repo.create_file(path_in_repo, commit_message, content, branch="main")
    print(f"✅ Created {path_in_repo} in {GITHUB_REPO}")
except Exception:
    existing = repo.get_contents(path_in_repo, ref="main")
    repo.update_file(existing.path, commit_message, content, existing.sha, branch="main")
    print(f"♻️ Updated {path_in_repo} in {GITHUB_REPO}")

print(f"✅ Wrote {len(out)} stories → data/{file_name}")
