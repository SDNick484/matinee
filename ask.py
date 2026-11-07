"""Ask: plain-language search over the catalog for editors (a tool-use loop over the read-only MCP tools).

The thread comes from the browser and is untrusted; only plain question/answer text is ever turned into model
messages. This module is imported at the bottom of app.py, once everything it needs is defined.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request

from flask import g, jsonify, request

from app import app, db, editor_required, make_err, clean


def env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        app.logger.warning("%s is not a whole number; using %s", name, default)
        return default


SEARCH_KEY = os.environ.get("ANTHROPIC_SEARCH_KEY", "").strip()
SEARCH_MODEL = os.environ.get("ANTHROPIC_SEARCH_MODEL", "").strip() or "claude-haiku-5-5"
DAILY_LIMIT = env_int("ASK_DAILY_LIMIT", 200)
MAX_ROUNDS = 5       # model calls per question; the last one is forced to finish
MAX_QUESTIONS = 5    # questions per thread
BUDGET_S = 60        # total time per question
CALL_TIMEOUT_S = 25  # time per API call
MAX_TOKENS = 2048
MAX_TOOL_RESULT_CHARS = 12000
INPUT_TOKEN_CEILING = 40000  # input tokens per question; past it, the next round is forced to finish
MAX_Q = 500
MAX_A = 1000
MAX_ANSWER = 600
MAX_TITLES = 12
UNAVAILABLE = "AI search is unavailable right now, try again"
INVALID = "That conversation isn't valid; start a new question."


def enabled():
    return bool(SEARCH_KEY)


class ClaudeAPIError(Exception):
    """The Messages API answered with an HTTP error status."""
    def __init__(self, status, detail):
        super().__init__(f"Claude API error {status}: {detail}")
        self.status, self.detail = status, detail


def messages_api(body, key, timeout):
    """POST body to the Messages API and return the parsed JSON. Only HTTP errors become ClaudeAPIError;
    timeouts and connection errors propagate unchanged."""
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ClaudeAPIError(e.code, e.read().decode(errors="replace")[:300])


class BadThread(ValueError):
    pass


def validate_thread(thread):
    """The browser's thread -> Claude messages. Raises BadThread with a short message."""
    if not isinstance(thread, list) or not thread:
        raise BadThread("Ask a question first.")
    out, questions = [], 0
    for i, entry in enumerate(thread):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise BadThread(INVALID)
        (key, val), = entry.items()
        if key not in ("q", "a") or not isinstance(val, str) or key != ("q" if i % 2 == 0 else "a"):
            raise BadThread(INVALID)
        text = clean(val)
        if key == "q":
            questions += 1
            if questions > MAX_QUESTIONS:
                raise BadThread("Start a new question to keep going.")
            if not text:
                raise BadThread("Ask a question first.")
            if len(text) > MAX_Q:
                raise BadThread(f"Keep questions under {MAX_Q} characters.")
            out.append({"role": "user", "content": text})
        else:
            if not text or len(val) > MAX_A:  # the API refuses empty assistant turns
                raise BadThread(INVALID)
            out.append({"role": "assistant", "content": text})
    if "q" not in thread[-1]:
        raise BadThread(INVALID)
    return out


def asked_today(con):
    """Questions that reached the model since local midnight (capped requests don't count)."""
    t = time.localtime()
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return con.execute("SELECT COUNT(*) FROM ask_log WHERE status != 'capped' AND ts >= ?", (start,)).fetchone()[0]


def log(editor, rounds, in_tok, out_tok, status):
    con = db()
    con.execute("INSERT INTO ask_log(ts, editor, rounds, input_tokens, output_tokens, status) VALUES (?,?,?,?,?,?)",
                (time.time(), editor, rounds, in_tok, out_tok, status))
    con.commit()


def answer(messages):
    """Stub until the tool loop lands (Task 5): (http_status, json_body)."""
    g.ask_stats = {"rounds": 0, "input_tokens": 0, "output_tokens": 0, "status": "ok"}
    return 200, {"answer": "", "items": [], "turns_left": 0}


def cross_site():
    origin = (request.headers.get("Origin") or "").strip()
    if origin and urllib.parse.urlparse(origin).netloc != request.host:
        return True
    return (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"


@app.post("/admin/api/ask")
@editor_required
def ask_route():
    if cross_site():
        return make_err(403, "Cross-site request refused")
    if g.editor == "api-key":
        return make_err(403, "Ask is for signed-in editors.")
    if not enabled():
        return make_err(503, "AI search isn't set up (no API key).")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "thread" not in body:
        return make_err(400, "Ask a question first.")
    try:
        messages = validate_thread(body["thread"])
    except BadThread as e:
        return make_err(400, str(e))
    if asked_today(db()) >= DAILY_LIMIT:
        log(g.editor, 0, 0, 0, "capped")
        return make_err(429, "Daily AI search limit reached; try again tomorrow.")
    try:
        status, payload = answer(messages)
    finally:
        s = g.ask_stats
        log(g.editor, s["rounds"], s["input_tokens"], s["output_tokens"], s["status"])
    return jsonify(payload), status
