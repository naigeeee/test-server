import os
import csv
import io
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

APP_NAME = "Event Feedback Survey"
FEEDBACK_API_KEY = os.environ.get("FEEDBACK_API_KEY", "")

app = FastAPI(title=APP_NAME, docs_url="/api/docs")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(APP_NAME)

DATABASE_URL = os.environ.get("DATABASE_URL")

db_pool = None


def _parse_db_url(url: str):
    parsed = urlparse(url)
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": user,
        "password": password,
        "database": parsed.path.lstrip("/"),
        "charset": "utf8mb4",
    }


if DATABASE_URL:
    try:
        import pymysql

        creds = _parse_db_url(DATABASE_URL)
        db_pool = pymysql.connect(
            host=creds["host"],
            port=creds["port"],
            user=creds["user"],
            password=creds["password"],
            database=creds["database"],
            charset=creds["charset"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        logger.info("Connected to OceanBase database.")
    except Exception as e:
        logger.warning("Database connection failed: %s — running on temporary memory.", e)
        db_pool = None
else:
    logger.info("No DATABASE_URL — running on temporary memory. Data will NOT persist.")


# ---------------------------------------------------------------------------
# In-memory fallback store (works with or without database)
# ---------------------------------------------------------------------------

memory_store: list[dict] = []


def _insert_feedback(row: dict) -> dict:
    if db_pool:
        try:
            with db_pool.cursor() as cur:
                cur.execute(
                    "INSERT INTO feedback_responses (respondent_name, rating, category, comments, event_name) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (row["respondent_name"], row["rating"], row["category"],
                     row["comments"], row["event_name"]),
                )
        except Exception as e:
            logger.error("DB insert failed: %s — falling back to memory.", e)
            row["id"] = len(memory_store) + 1
            row["created_at"] = datetime.now(timezone.utc).isoformat()
            memory_store.append(row)
            return row
        row["id"] = None
        row["created_at"] = datetime.now(timezone.utc).isoformat()
        return row
    else:
        row["id"] = len(memory_store) + 1
        row["created_at"] = datetime.now(timezone.utc).isoformat()
        memory_store.append(row)
        return row


def _get_all_feedback() -> list[dict]:
    if db_pool:
        try:
            with db_pool.cursor() as cur:
                cur.execute(
                    "SELECT id, respondent_name, rating, category, comments, event_name, "
                    "created_at FROM feedback_responses ORDER BY created_at DESC"
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("DB read failed: %s — using memory.", e)
    return list(reversed(memory_store))


def _get_feedback_since(since: str) -> list[dict]:
    """Return feedback created after the given ISO timestamp."""
    if db_pool:
        try:
            with db_pool.cursor() as cur:
                cur.execute(
                    "SELECT id, respondent_name, rating, category, comments, event_name, "
                    "created_at FROM feedback_responses WHERE created_at > %s ORDER BY created_at DESC",
                    (since,),
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("DB read (since) failed: %s — using memory.", e)
    cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
    return [r for r in reversed(memory_store)
            if datetime.fromisoformat(r.get("created_at", "2000-01-01T00:00:00+00:00").replace("Z", "+00:00")) > cutoff]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FeedbackCreate(BaseModel):
    respondent_name: str = Field(default="Anonymous", max_length=255)
    rating: int = Field(..., ge=1, le=5)
    category: str = Field(..., max_length=100)
    comments: Optional[str] = Field(default=None, max_length=2000)
    event_name: str = Field(..., max_length=255)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@app.get("/api/info")
def info():
    return {
        "app": APP_NAME,
        "storage": "database" if db_pool else "temporary-memory",
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


@app.post("/api/feedback")
def submit_feedback(fb: FeedbackCreate):
    row = fb.model_dump()
    saved = _insert_feedback(row)
    return {"status": "submitted", "feedback": saved}


@app.get("/api/feedback")
def list_feedback():
    return {"feedback": _get_all_feedback(), "count": len(_get_all_feedback())}


# ---------------------------------------------------------------------------
# API key verification helper
# ---------------------------------------------------------------------------

def _verify_api_key(x_api_key: Optional[str] = Header(None)):
    if not FEEDBACK_API_KEY:
        raise HTTPException(status_code=500, detail="FEEDBACK_API_KEY not configured on the server.")
    if x_api_key != FEEDBACK_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ---------------------------------------------------------------------------
# Export endpoints (Google Apps Script calls these)
# ---------------------------------------------------------------------------

@app.get("/api/feedback/export")
def export_feedback(x_api_key: Optional[str] = Header(None), since: Optional[str] = None):
    """Export feedback as JSON. Pass ?since=ISO_TIMESTAMP for incremental sync."""
    _verify_api_key(x_api_key)
    if since:
        rows = _get_feedback_since(since)
    else:
        rows = _get_all_feedback()
    return {"feedback": rows, "count": len(rows)}


@app.get("/api/feedback/export/csv")
def export_feedback_csv(x_api_key: Optional[str] = Header(None)):
    """Export all feedback as a downloadable CSV file."""
    _verify_api_key(x_api_key)
    rows = _get_all_feedback()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "respondent_name", "rating", "category", "comments", "event_name", "created_at"])
    for r in rows:
        writer.writerow([r.get("id"), r.get("respondent_name", ""), r.get("rating", ""),
                         r.get("category", ""), r.get("comments", ""),
                         r.get("event_name", ""), r.get("created_at", "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback_export.csv"},
    )


@app.post("/api/feedback/sync")
def sync_feedback_to_sheet(payload: dict, x_api_key: Optional[str] = Header(None)):
    """
    Receives rows from Google Apps Script and returns them for Sheet writing.
    This is a read-only sync: Apps Script pulls from /api/feedback/export.
    This endpoint exists for future push-from-Sheet capability.
    """
    _verify_api_key(x_api_key)
    return {
        "status": "ok",
        "message": "Use GET /api/feedback/export to pull all feedback into your Sheet.",
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event Feedback Survey</title>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400;500;700&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box;margin:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:'Satoshi',ui-sans-serif,system-ui,-apple-system,sans-serif;background:#f5f5f4;padding:40px 20px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:520px;width:100%}
  .header{text-align:center;margin-bottom:32px}
  .pill{display:inline-flex;align-items:center;gap:6px;background:#f0fdfa;padding:6px 14px;border-radius:20px;margin-bottom:16px}
  .pill-dot{width:6px;height:6px;border-radius:50%;background:#b0b0b0}
  .pill-dot.ok{background:#10b981}
  .pill-text{font-size:12px;font-weight:500;color:#0d9488;letter-spacing:.02em}
  h1{font-size:30px;font-weight:700;color:#1c1917;letter-spacing:-.025em;line-height:1.2}
  .subtitle{font-size:14px;color:#78716c;margin-top:8px}
  .card{background:#fff;border-radius:12px;padding:28px;box-shadow:0 0 0 1px rgba(0,0,0,.04),0 2px 8px rgba(0,0,0,.03);margin-bottom:20px}
  .card h2{font-size:16px;font-weight:600;color:#1c1917;margin-bottom:20px}
  label{display:block;font-size:13px;font-weight:500;color:#57534e;margin-bottom:6px}
  input,select,textarea{width:100%;padding:10px 12px;border:1px solid #e7e5e4;border-radius:8px;font-size:14px;font-family:inherit;color:#1c1917;background:#fff;transition:border .15s}
  input:focus,select:focus,textarea:focus{outline:none;border-color:#0d9488;box-shadow:0 0 0 3px rgba(13,148,136,.1)}
  textarea{resize:vertical;min-height:80px}
  .field{margin-bottom:16px}
  .stars{display:flex;gap:4px}
  .stars input{display:none}
  .stars label{font-size:28px;color:#d4d4d4;cursor:pointer;transition:color .15s;margin-bottom:0}
  .stars label:hover,.stars label:hover~label,.stars input:checked~label{color:#f59e0b}
  .stars{direction:rtl;unicode-bidi:bidi-override}
  .btn{display:inline-flex;align-items:center;justify-content:center;width:100%;padding:12px 24px;background:#0d9488;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;font-family:inherit;cursor:pointer;transition:background .15s;margin-top:4px}
  .btn:hover{background:#0f766e}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .msg{padding:12px 16px;border-radius:8px;font-size:13px;margin-bottom:16px;display:none}
  .msg.ok{display:block;background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
  .msg.err{display:block;background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
  .local-badge{display:inline-flex;align-items:center;gap:4px;background:#fef3c7;color:#92400e;font-size:11px;font-weight:600;padding:4px 10px;border-radius:12px;margin-bottom:12px}
  .history-card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 0 0 1px rgba(0,0,0,.04);margin-top:20px}
  .history-card h3{font-size:15px;font-weight:600;color:#1c1917;margin-bottom:12px}
  .fb-item{border-bottom:1px solid #f3f4f6;padding:12px 0}
  .fb-item:last-child{border-bottom:none}
  .fb-meta{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
  .fb-name{font-size:13px;font-weight:600;color:#1c1917}
  .fb-date{font-size:11px;color:#a8a29e}
  .fb-stars{color:#f59e0b;font-size:13px;letter-spacing:1px}
  .fb-cat{display:inline-block;font-size:11px;font-weight:500;background:#f0fdfa;color:#0d9488;padding:2px 8px;border-radius:10px;margin-bottom:4px}
  .fb-comments{font-size:13px;color:#57534e;line-height:1.5}
  .empty{text-align:center;padding:20px;color:#a8a29e;font-size:13px}
  .foot{text-align:center;font-size:12px;color:#c4c4cc;margin-top:24px}
  @media(max-width:480px){h1{font-size:25px}.card{padding:20px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="pill">
      <div class="pill-dot ok"></div>
      <span class="pill-text" id="storageMode">Checking storage&hellip;</span>
    </div>
    <h1>Event Feedback Survey</h1>
    <p class="subtitle">Share your thoughts so we can improve future events</p>
  </div>

  <div class="msg" id="msg"></div>

  <div class="card">
    <h2>Submit Your Feedback</h2>
    <form id="fbForm">
      <div class="field">
        <label for="name">Your Name</label>
        <input type="text" id="name" placeholder="e.g. Jane Doe" maxlength="255">
      </div>
      <div class="field">
        <label for="event">Event Name *</label>
        <input type="text" id="event" placeholder="e.g. Q3 Town Hall" maxlength="255" required>
      </div>
      <div class="field">
        <label>Rating *</label>
        <div class="stars">
          <input type="radio" id="star5" name="rating" value="5"><label for="star5">&#9733;</label>
          <input type="radio" id="star4" name="rating" value="4"><label for="star4">&#9733;</label>
          <input type="radio" id="star3" name="rating" value="3"><label for="star3">&#9733;</label>
          <input type="radio" id="star2" name="rating" value="2"><label for="star2">&#9733;</label>
          <input type="radio" id="star1" name="rating" value="1"><label for="star1">&#9733;</label>
        </div>
      </div>
      <div class="field">
        <label for="category">Category *</label>
        <select id="category" required>
          <option value="">Select a category</option>
          <option value="Content & Relevance">Content & Relevance</option>
          <option value="Venue & Logistics">Venue & Logistics</option>
          <option value="Speaker Quality">Speaker Quality</option>
          <option value="Networking">Networking</option>
          <option value="Overall Experience">Overall Experience</option>
          <option value="Other">Other</option>
        </select>
      </div>
      <div class="field">
        <label for="comments">Comments</label>
        <textarea id="comments" placeholder="Tell us what you think..." maxlength="2000"></textarea>
      </div>
      <button type="submit" class="btn" id="submitBtn">Submit Feedback</button>
    </form>
  </div>

  <div class="history-card">
    <h3>Recent Feedback</h3>
    <div id="history"><p class="empty">Loading feedback&hellip;</p></div>
  </div>

  <p class="foot">Powered by Substrait</p>
</div>

<script>
(function(){
  var msg=document.getElementById("msg");

  fetch("/api/info").then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById("storageMode");
    if(d.storage==="database"){
      el.textContent="Permanent storage (database)";
    }else{
      el.insertAdjacentHTML("afterbegin","<span class='local-badge'>&#9888; Local test mode &mdash; data is not saved</span> ");
      el.textContent="Temporary memory";
    }
  });

  function loadHistory(){
    fetch("/api/feedback").then(function(r){return r.json()}).then(function(d){
      var el=document.getElementById("history");
      if(!d.feedback||d.feedback.length===0){el.innerHTML='<p class="empty">No feedback yet. Be the first!</p>';return;}
      var h="";
      d.feedback.forEach(function(f){
        var stars="";for(var i=0;i<5;i++)stars+=i<f.rating?"&#9733;":"&#9734;";
        var date=f.created_at?f.created_at.substring(0,10):"";
        h+='<div class="fb-item">';
        h+='<div class="fb-meta"><span class="fb-name">'+esc(f.respondent_name)+"</span><span class='fb-date'>"+date+"</span></div>";
        h+='<div class="fb-stars">'+stars+'</div>';
        h+='<div class="fb-cat">'+esc(f.category)+'</div><br>';
        if(f.comments)h+='<div class="fb-comments">'+esc(f.comments)+'</div>';
        h+="</div>";
      });
      el.innerHTML=h;
    });
  }
  loadHistory();

  function esc(s){var d=document.createElement("div");d.appendChild(document.createTextNode(s));return d.innerHTML;}

  document.getElementById("fbForm").addEventListener("submit",function(e){
    e.preventDefault();
    var rating=document.querySelector('input[name="rating"]:checked');
    if(!rating){showMsg("Please select a rating.",true);return;}
    var cat=document.getElementById("category").value;
    if(!cat){showMsg("Please select a category.",true);return;}
    var payload={
      respondent_name:document.getElementById("name").value.trim()||"Anonymous",
      rating:parseInt(rating.value),
      category:cat,
      comments:document.getElementById("comments").value.trim()||null,
      event_name:document.getElementById("event").value.trim()
    };
    document.getElementById("submitBtn").disabled=true;
    fetch("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
    .then(function(r){return r.json()}).then(function(d){
      if(d.status==="submitted"){
        showMsg("Thank you! Your feedback has been recorded.",false);
        document.getElementById("fbForm").reset();
        loadHistory();
      }else{
        showMsg("Something went wrong. Please try again.",true);
      }
      document.getElementById("submitBtn").disabled=false;
    }).catch(function(){
      showMsg("Network error. Please try again.",true);
      document.getElementById("submitBtn").disabled=false;
    });
  });

  function showMsg(text,isErr){
    msg.textContent=text;
    msg.className=isErr?"msg err":"msg ok";
    setTimeout(function(){msg.className="msg"},5000);
  }
})();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def homepage():
    return PAGE
