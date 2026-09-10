"""
PDFF FSM Master — Data analysis tool.
Import Google Sheets data via CSV paste/upload for analysis.
"""

import csv
import io
import os
from datetime import datetime, timezone
from collections import Counter

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

APP_NAME = "PDFF - FSM - Master"

app = FastAPI(title=APP_NAME, docs_url="/api/docs")

_uploaded: dict[str, dict] = {}


class PasteRequest(BaseModel):
    csv_text: str


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@app.get("/api/info")
def info():
    return {
        "app": APP_NAME,
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "main_loaded": "main" in _uploaded,
        "booking_loaded": "booking" in _uploaded,
    }


def _parse_csv(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader]
    while rows and all(c.strip() == "" for c in rows[-1]):
        rows.pop()
    return rows


@app.post("/api/upload")
def upload_main(req: PasteRequest):
    rows = _parse_csv(req.csv_text)
    if len(rows) < 2:
        return {"error": "Need at least a header row and one data row"}
    _uploaded["main"] = {"headers": rows[0], "rows": rows[1:]}
    return {"ok": True, "sheet": "main", "rows": len(rows) - 1, "cols": len(rows[0])}


@app.post("/api/upload/booking")
def upload_booking(req: PasteRequest):
    rows = _parse_csv(req.csv_text)
    if len(rows) < 2:
        return {"error": "Need at least a header row and one data row"}
    _uploaded["booking"] = {"headers": rows[0], "rows": rows[1:]}
    return {"ok": True, "sheet": "booking", "rows": len(rows) - 1, "cols": len(rows[0])}


@app.post("/api/upload/file")
async def upload_file(file: UploadFile = File(...), sheet: str = "main"):
    content = await file.read()
    text = content.decode("utf-8-sig")
    rows = _parse_csv(text)
    if len(rows) < 2:
        return {"error": "Need at least a header row and one data row"}
    _uploaded[sheet] = {"headers": rows[0], "rows": rows[1:]}
    return {"ok": True, "sheet": sheet, "rows": len(rows) - 1, "cols": len(rows[0])}


@app.delete("/api/upload/{sheet}")
def clear_sheet(sheet: str):
    _uploaded.pop(sheet, None)
    return {"ok": True, "cleared": sheet}


@app.get("/api/data/{sheet}")
def get_data(sheet: str, q: str = "", col: str = "", sort: str = "", limit: int = 500, offset: int = 0):
    data = _uploaded.get(sheet)
    if not data:
        return {"headers": [], "rows": [], "total": 0}
    headers = data["headers"]
    rows = data["rows"]
    if col and col in headers:
        ci = headers.index(col)
        rows = [r for r in rows if ci < len(r) and q.lower() in r[ci].lower()]
    elif q:
        rows = [r for r in rows if any(q.lower() in c.lower() for c in r)]
    if sort:
        desc = sort.startswith("-")
        sort_col = sort.lstrip("-")
        if sort_col in headers:
            si = headers.index(sort_col)
            rows = sorted(rows, key=lambda r: r[si] if si < len(r) else "", reverse=desc)
    total = len(rows)
    return {"headers": headers, "rows": rows[offset:offset + limit], "total": total}


@app.get("/api/analysis/{sheet}")
def analyze_sheet(sheet: str):
    data = _uploaded.get(sheet)
    if not data:
        return {"error": "no data"}
    headers = data["headers"]
    rows = data["rows"]
    stats = {}
    for i, h in enumerate(headers):
        vals = [r[i] for r in rows if i < len(r) and r[i].strip()]
        nums = []
        for v in vals:
            try:
                nums.append(float(v.replace(",", "").replace(" ", "")))
            except ValueError:
                pass
        if nums:
            stats[h] = {
                "type": "numeric",
                "count": len(nums),
                "sum": round(sum(nums), 2),
                "avg": round(sum(nums) / len(nums), 2),
                "min": round(min(nums), 2),
                "max": round(max(nums), 2),
            }
        elif vals:
            top = Counter(vals).most_common(10)
            stats[h] = {
                "type": "categorical",
                "count": len(vals),
                "unique": len(set(vals)),
                "top": top,
            }
    return {"sheet": sheet, "total_rows": len(rows), "columns": len(headers), "stats": stats}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDFF - FSM - Master</title>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400;500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;font-family:'Satoshi',system-ui,sans-serif;background:#f5f5f4;color:#1c1917}
.topbar{background:#fff;border-bottom:1px solid #e5e5e5;padding:14px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.topbar h1{font-size:18px;font-weight:700}
.topbar .pill{display:inline-flex;align-items:center;gap:6px;background:#f0fdfa;padding:4px 12px;border-radius:16px;font-size:12px;font-weight:500;color:#0d9488}
.topbar .pill .dot{width:6px;height:6px;border-radius:50%;background:#10b981}
.tabs{display:flex;background:#fff;border-bottom:1px solid #e5e5e5;padding:0 24px}
.tabs button{padding:12px 20px;border:none;background:none;font-family:inherit;font-size:14px;font-weight:500;color:#78716c;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s}
.tabs button:hover{color:#1c1917}
.tabs button.active{color:#0d9488;border-bottom-color:#0d9488}
.container{max-width:1400px;margin:0 auto;padding:24px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 0 0 1px rgba(0,0,0,.04)}
.stat-card .label{font-size:12px;color:#a8a29e;font-weight:500;text-transform:uppercase;letter-spacing:.04em}
.stat-card .value{font-size:28px;font-weight:700;margin-top:4px}
.toolbar{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.toolbar input{padding:8px 14px;border:1px solid #d4d4d8;border-radius:8px;font-family:inherit;font-size:14px;width:320px;outline:none}
.toolbar input:focus{border-color:#0d9488}
.toolbar select{padding:8px 12px;border:1px solid #d4d4d8;border-radius:8px;font-family:inherit;font-size:14px;background:#fff}
.toolbar .count{font-size:13px;color:#78716c;margin-left:auto}
.table-wrap{background:#fff;border-radius:10px;box-shadow:0 0 0 1px rgba(0,0,0,.04);overflow:auto;max-height:70vh}
table{width:100%;border-collapse:collapse;font-size:13px}
th{position:sticky;top:0;background:#fafaf9;border-bottom:2px solid #e5e5e5;padding:10px 12px;text-align:left;font-weight:600;color:#52525b;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{background:#f0f0ef}
th .sort{margin-left:4px;color:#d4d4d8;font-size:10px}
th.sorted .sort{color:#0d9488}
td{padding:8px 12px;border-bottom:1px solid #f3f4f6;white-space:nowrap;color:#404040}
tr:hover td{background:#fafaf9}
.upload-zone{background:#fff;border:2px dashed #d4d4d8;border-radius:10px;padding:32px;text-align:center;margin-bottom:24px;cursor:pointer;transition:border-color .15s}
.upload-zone:hover,.upload-zone.dragover{border-color:#0d9488;background:#f0fdfa}
.upload-zone h3{font-size:14px;font-weight:600;margin-bottom:6px}
.upload-zone p{font-size:13px;color:#78716c;margin-bottom:12px}
.upload-zone input[type=file]{display:none}
.upload-zone .or{font-size:12px;color:#a8a29e;margin:12px 0}
.upload-zone textarea{width:100%;height:120px;border:1px solid #d4d4d8;border-radius:8px;padding:10px;font-family:monospace;font-size:12px;resize:vertical;outline:none}
.upload-zone textarea:focus{border-color:#0d9488}
.upload-zone button{margin-top:10px;padding:8px 20px;background:#0d9488;color:#fff;border:none;border-radius:8px;font-family:inherit;font-size:13px;font-weight:500;cursor:pointer}
.upload-zone button:hover{background:#0f766e}
.upload-zone .done{color:#10b981;font-size:13px;font-weight:500}
.hidden{display:none}
.error-box{background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;color:#991b1b;margin-bottom:16px}
.bar-chart{display:flex;flex-direction:column;gap:6px}
.bar-row{display:flex;align-items:center;gap:10px}
.bar-label{width:160px;font-size:12px;color:#52525b;text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{flex:1;height:22px;background:#f3f4f6;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;background:linear-gradient(90deg,#0d9488,#14b8a6);border-radius:4px;transition:width .3s}
.bar-val{width:50px;font-size:12px;color:#78716c;font-weight:500}
.analysis-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
@media(max-width:900px){.analysis-grid{grid-template-columns:1fr}}
.analysis-card{background:#fff;border-radius:10px;padding:20px;box-shadow:0 0 0 1px rgba(0,0,0,.04)}
.analysis-card h3{font-size:14px;font-weight:600;margin-bottom:12px}
.numeric-stat{display:flex;gap:16px;flex-wrap:wrap;margin-top:6px}
.numeric-stat .item{font-size:12px;color:#78716c}
.numeric-stat .item strong{color:#1c1917}
.sheet-status{display:flex;gap:12px;margin-bottom:20px}
.sheet-badge{padding:6px 14px;border-radius:8px;font-size:13px;font-weight:500}
.sheet-badge.loaded{background:#f0fdfa;color:#0d9488;border:1px solid #99f6e4}
.sheet-badge.empty{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
.btn-clear{padding:6px 14px;background:#fee2e2;color:#991b1b;border:none;border-radius:6px;font-size:12px;cursor:pointer}
.btn-clear:hover{background:#fecaca}
.instructions{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:16px;margin-bottom:20px;font-size:13px;color:#92400e;line-height:1.6}
.instructions h4{margin-bottom:8px}
.instructions ol{margin-left:20px}
.instructions code{background:#fef3c7;padding:1px 5px;border-radius:3px;font-size:12px}
</style>
</head>
<body>

<div class="topbar">
  <h1>PDFF - FSM - Master</h1>
  <span class="pill"><span class="dot"></span>Live</span>
</div>

<div class="tabs">
  <button class="active" onclick="showTab('main',this)">Main</button>
  <button onclick="showTab('booking',this)">Booking v2</button>
  <button onclick="showTab('analysis',this)">Analysis</button>
</div>

<div class="container">
  <div id="error-box" class="error-box hidden"></div>

  <div id="tab-main" class="tab-content">
    <div class="instructions">
      <h4>How to import Main sheet data</h4>
      <ol>
        <li>Open the <a href="https://docs.google.com/spreadsheets/d/1cEdsjp1VfEsqYafijLvIWWoS0euZcg-x1Z1Tl2bxwxE/edit#gid=0" target="_blank">Google Sheet</a> &rarr; <strong>Main</strong> tab</li>
        <li>Select all data (Ctrl+A), then Copy (Ctrl+C)</li>
        <li>Paste into the text area below and click Upload</li>
      </ol>
    </div>
    <div id="main-upload" class="upload-zone">
      <h3>Upload Main Sheet Data</h3>
      <p>Drag & drop a CSV file or paste data below</p>
      <input type="file" id="main-file" accept=".csv,.tsv,.txt" onchange="handleFile('main',this.files[0])">
      <button onclick="document.getElementById('main-file').click()">Choose CSV File</button>
      <div class="or">&mdash; or paste CSV/TSV data &mdash;</div>
      <textarea id="main-paste" placeholder="Paste CSV or tab-separated data from Google Sheets here..."></textarea>
      <button onclick="submitPaste('main')">Upload Pasted Data</button>
    </div>
    <div id="main-data" class="hidden">
      <div id="main-stats" class="stats-grid"></div>
      <div class="toolbar">
        <input type="text" id="main-search" placeholder="Search Main..." oninput="filterTable('main')">
        <select id="main-col-filter" onchange="filterTable('main')"><option value="">All columns</option></select>
        <span class="count" id="main-count"></span>
        <button class="btn-clear" onclick="clearSheet('main')">Clear</button>
      </div>
      <div class="table-wrap"><table id="main-table"><thead id="main-thead"></thead><tbody id="main-tbody"></tbody></table></div>
    </div>
  </div>

  <div id="tab-booking" class="tab-content hidden">
    <div class="instructions">
      <h4>How to import Booking v2 data</h4>
      <ol>
        <li>Open the <a href="https://docs.google.com/spreadsheets/d/1cEdsjp1VfEsqYafijLvIWWoS0euZcg-x1Z1Tl2bxwxE/edit#gid=228589412" target="_blank">Google Sheet</a> &rarr; <strong>Booking v2</strong> tab</li>
        <li>Select all data (Ctrl+A), then Copy (Ctrl+C)</li>
        <li>Paste into the text area below and click Upload</li>
      </ol>
    </div>
    <div id="booking-upload" class="upload-zone">
      <h3>Upload Booking v2 Data</h3>
      <p>Drag & drop a CSV file or paste data below</p>
      <input type="file" id="booking-file" accept=".csv,.tsv,.txt" onchange="handleFile('booking',this.files[0])">
      <button onclick="document.getElementById('booking-file').click()">Choose CSV File</button>
      <div class="or">&mdash; or paste CSV/TSV data &mdash;</div>
      <textarea id="booking-paste" placeholder="Paste CSV or tab-separated data from Google Sheets here..."></textarea>
      <button onclick="submitPaste('booking')">Upload Pasted Data</button>
    </div>
    <div id="booking-data" class="hidden">
      <div id="booking-stats" class="stats-grid"></div>
      <div class="toolbar">
        <input type="text" id="booking-search" placeholder="Search Booking v2..." oninput="filterTable('booking')">
        <select id="booking-col-filter" onchange="filterTable('booking')"><option value="">All columns</option></select>
        <span class="count" id="booking-count"></span>
        <button class="btn-clear" onclick="clearSheet('booking')">Clear</button>
      </div>
      <div class="table-wrap"><table id="booking-table"><thead id="booking-thead"></thead><tbody id="booking-tbody"></tbody></table></div>
    </div>
  </div>

  <div id="tab-analysis" class="tab-content hidden">
    <div id="analysis-content"><p style="text-align:center;padding:60px;color:#a8a29e">Upload data in the Main or Booking tabs first.</p></div>
  </div>
</div>

<script>
let sheets={};
let sortState={};

function showTab(name,btn){
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.add('hidden'));
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.remove('hidden');
  if(btn)btn.classList.add('active');
  if(name==='analysis')loadAnalysis();
}

function showError(msg){
  const e=document.getElementById('error-box');
  e.textContent=msg;e.classList.remove('hidden');
  setTimeout(()=>e.classList.add('hidden'),5000);
}

function handleFile(sheet,file){
  if(!file)return;
  const r=new FileReader();
  r.onload=e=>uploadCSV(sheet,e.target.result);
  r.readAsText(file);
}

function submitPaste(sheet){
  const t=document.getElementById(sheet+'-paste').value.trim();
  if(!t)return showError('Paste some data first');
  uploadCSV(sheet,t);
}

function uploadCSV(sheet,text){
  const ep=sheet==='booking'?'/api/upload/booking':'/api/upload';
  fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv_text:text})})
  .then(r=>r.json())
  .then(d=>{
    if(d.error)return showError(d.error);
    document.getElementById(sheet+'-upload').classList.add('hidden');
    document.getElementById(sheet+'-data').classList.remove('hidden');
    loadSheet(sheet);
  })
  .catch(e=>showError('Upload failed: '+e.message));
}

function loadSheet(sheet){
  fetch('/api/data/'+sheet+'?limit=1000')
  .then(r=>r.json())
  .then(d=>{
    sheets[sheet]=d;
    renderTable(sheet,d);
    updateColFilter(sheet,d.headers);
    document.getElementById(sheet+'-stats').innerHTML=renderStats(d);
  });
}

function renderStats(d){
  if(!d.rows||!d.rows.length)return '';
  return '<div class="stat-card"><div class="label">Rows</div><div class="value">'+d.total+'</div></div>'+
    '<div class="stat-card"><div class="label">Columns</div><div class="value">'+d.headers.length+'</div></div>';
}

function updateColFilter(sheet,headers){
  const s=document.getElementById(sheet+'-col-filter');
  s.innerHTML='<option value="">All columns</option>'+headers.map(h=>'<option value="'+h+'">'+h+'</option>').join('');
}

function filterTable(sheet){
  const q=document.getElementById(sheet+'-search').value;
  const col=document.getElementById(sheet+'-col-filter').value;
  const sort=sortState[sheet]||'';
  fetch('/api/data/'+sheet+'?q='+encodeURIComponent(q)+'&col='+encodeURIComponent(col)+'&sort='+encodeURIComponent(sort)+'&limit=1000')
  .then(r=>r.json())
  .then(d=>{sheets[sheet]=d;renderTable(sheet,d);});
}

function sortTable(sheet,col){
  const c=sortState[sheet]||'';
  if(c===col)sortState[sheet]='-'+col;
  else if(c==='-'+col)sortState[sheet]='';
  else sortState[sheet]=col;
  filterTable(sheet);
}

function renderTable(sheet,d){
  const th=document.getElementById(sheet+'-thead');
  const tb=document.getElementById(sheet+'-tbody');
  const sort=sortState[sheet]||'';
  th.innerHTML='<tr>'+d.headers.map(h=>{
    const cls=sort===h||sort==='-'+h?'sorted':'';
    const arrow=sort===h?' &#9650;':sort==='-'+h?' &#9660;':' &#8693;';
    return '<th class="'+cls+'" onclick="sortTable(\''+sheet+'\',\''+h.replace(/'/g,"\\'")+'\')">'+h+'<span class="sort">'+arrow+'</span></th>';
  }).join('')+'</tr>';
  tb.innerHTML=d.rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('');
  document.getElementById(sheet+'-count').textContent=d.total+' rows';
}

function esc(s){if(!s)return '';return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function clearSheet(sheet){
  if(!confirm('Clear '+sheet+' data?'))return;
  fetch('/api/upload/'+sheet,{method:'DELETE'})
  .then(()=>{
    document.getElementById(sheet+'-upload').classList.remove('hidden');
    document.getElementById(sheet+'-data').classList.add('hidden');
    document.getElementById(sheet+'-paste').value='';
  });
}

function loadAnalysis(){
  const el=document.getElementById('analysis-content');
  el.innerHTML='<p style="text-align:center;padding:60px;color:#a8a29e">Loading...</p>';
  Promise.all([
    fetch('/api/analysis/main').then(r=>r.json()),
    fetch('/api/analysis/booking').then(r=>r.json())
  ]).then(([main,booking])=>{
    let html='<div class="analysis-grid">';
    let hasData=false;
    if(main.stats&&Object.keys(main.stats).length){html+=renderAnalysisCard('Main',main);hasData=true;}
    if(booking.stats&&Object.keys(booking.stats).length){html+=renderAnalysisCard('Booking v2',booking);hasData=true;}
    if(!hasData)html='<p style="text-align:center;padding:60px;color:#a8a29e">No data to analyze. Upload CSV in the Main or Booking tabs first.</p>';
    else html+='</div>';
    el.innerHTML=html;
  });
}

function renderAnalysisCard(title,data){
  let h='<div class="analysis-card"><h3>'+title+' ('+data.total_rows+' rows, '+data.columns+' cols)</h3>';
  for(const[col,stat]of Object.entries(data.stats).slice(0,10)){
    if(stat.type==='categorical'&&stat.top){
      const max=Math.max(...stat.top.map(t=>t[1]));
      h+='<div style="margin-bottom:14px"><div style="font-size:12px;font-weight:600;color:#52525b;margin-bottom:6px">'+col+' <span style="color:#a8a29e;font-weight:400">('+stat.unique+' unique)</span></div>';
      h+='<div class="bar-chart">';
      for(const[val,cnt]of stat.top.slice(0,8)){
        const pct=max>0?(cnt/max*100):0;
        h+='<div class="bar-row"><div class="bar-label">'+esc(val||'(empty)')+'</div><div class="bar-track"><div class="bar-fill" style="width:'+pct+'%"></div></div><div class="bar-val">'+cnt+'</div></div>';
      }
      h+='</div></div>';
    }else if(stat.type==='numeric'){
      h+='<div style="margin-bottom:14px"><div style="font-size:12px;font-weight:600;color:#52525b;margin-bottom:4px">'+col+'</div>';
      h+='<div class="numeric-stat">';
      h+='<div class="item">Avg: <strong>'+stat.avg.toLocaleString()+'</strong></div>';
      h+='<div class="item">Min: <strong>'+stat.min.toLocaleString()+'</strong></div>';
      h+='<div class="item">Max: <strong>'+stat.max.toLocaleString()+'</strong></div>';
      h+='<div class="item">Sum: <strong>'+stat.sum.toLocaleString()+'</strong></div>';
      h+='</div></div>';
    }
  }
  h+='</div>';
  return h;
}

document.addEventListener('DOMContentLoaded',function(){
  ['main','booking'].forEach(sheet=>{
    const zone=document.getElementById(sheet+'-upload');
    if(zone){
      zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('dragover');});
      zone.addEventListener('dragleave',()=>zone.classList.remove('dragover'));
      zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('dragover');if(e.dataTransfer.files[0])handleFile(sheet,e.dataTransfer.files[0]);});
    }
  });
  fetch('/api/info').then(r=>r.json()).then(d=>{
    if(d.main_loaded){
      document.getElementById('main-upload').classList.add('hidden');
      document.getElementById('main-data').classList.remove('hidden');
      loadSheet('main');
    }
    if(d.booking_loaded){
      document.getElementById('booking-upload').classList.add('hidden');
      document.getElementById('booking-data').classList.remove('hidden');
      loadSheet('booking');
    }
  });
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def homepage():
    return PAGE
