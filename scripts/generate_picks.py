#!/usr/bin/env python3
"""Baseline transparent market-consensus analysis; not an independent prediction model."""
import json, os, statistics, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"data"/"picks.json"
def api(path,params,key):
 url="https://api.the-odds-api.com/v4/"+path+"?"+urlencode({**params,"apiKey":key})
 with urlopen(Request(url,headers={"User-Agent":"PicksPro/1.0"}),timeout=35) as r:return json.load(r)
def write(d):
 OUT.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=OUT.parent,delete=False) as f:json.dump(d,f,ensure_ascii=False,indent=2);name=f.name
 os.replace(name,OUT)
def empty(status,message):write({"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat(),"status":status,"message":message,"events_analyzed":0,"best_edge":None,"sports":[],"picks":[],"method":"Consenso sin margen; no es modelo independiente."})
def main():
 key=os.getenv("ODDS_API_KEY")
 if not key:empty("needs_configuration","Falta ODDS_API_KEY; no se inventaron datos.");return
 sports=[s for s in api("sports",{"all":"false"},key) if s.get("active")]; picks=[];events_seen=set()
 for sport in sports:
  try: events=api("sports/"+sport["key"]+"/odds",{"regions":os.getenv("ODDS_REGIONS","us,mx"),"markets":"h2h,spreads,totals","oddsFormat":"decimal","dateFormat":"iso"},key)
  except HTTPError as e:
   if e.code in (404,422):continue
   raise
  for event in events:
   events_seen.add(event.get("id")); groups={}
   for book in event.get("bookmakers",[]):
    for market in book.get("markets",[]):
     for o in market.get("outcomes",[]):
      price=o.get("price")
      if not isinstance(price,(int,float)) or price<=1:continue
      line=round(o["point"],3) if o.get("point") is not None else None
      groups.setdefault((market["key"],line),{}).setdefault(book.get("key",""),[]).append(o)
   for (market,line),books in groups.items():
    fair={};best={}
    for book,outs in books.items():
     probs=[1/float(o["price"]) for o in outs];total=sum(probs)
     if not total:continue
     for o,prob in zip(outs,probs):
      k=(o.get("name",""),o.get("point"));fair.setdefault(k,[]).append(prob/total)
      if k not in best or o["price"]>best[k]["price"]:best[k]={"price":o["price"],"book":book}
    for k,offer in best.items():
     vals=fair.get(k,[])
     if not vals:continue
     p=sum(vals)/len(vals);odds=float(offer["price"]);imp=1/odds;edge=p-imp
     spread=statistics.pstdev(vals) if len(vals)>1 else .25
     conf=min(1,len(vals)/5)*max(.2,1-2*spread);kelly=max(0,((odds-1)*p-(1-p))/(odds-1))
     value=edge>=.03 and conf>=.55 and len(vals)>=2
     picks.append({"event":event.get("home_team","")+" vs "+event.get("away_team",""),"sport_key":sport["key"],"sport":sport.get("title",sport["key"]),"commence_time":event.get("commence_time"),"market":market,"selection":k[0]+(f" ({k[1]:+g})" if k[1] is not None else ""),"bookmaker":offer["book"],"odds":odds,"implied_probability":imp,"estimated_probability":p,"edge":edge,"confidence":conf,"stake_units":min(1,kelly*25) if value else 0,"decision":"VALUE" if value else "NO BET","books_count":len(vals),"estimation":"market_consensus_no_vig"})
 picks.sort(key=lambda p:(p["decision"]=="VALUE",p["edge"]),reverse=True)
 write({"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat(),"status":"ok","message":"Probabilidad estimada = consenso sin margen; no es una predicción independiente.","events_analyzed":len(events_seen),"best_edge":max((p["edge"] for p in picks),default=None),"sports":[{"key":s["key"],"title":s.get("title",s["key"])} for s in sports],"picks":picks[:500],"method":"NO BET bajo 3% edge, 55% confianza o menos de 2 casas. Stake = cuarto Kelly, máximo 1 unidad."})
 print("Events:",len(events_seen),"candidates:",len(picks))
if __name__=="__main__":
 try:main()
 except Exception as e:empty("error",str(e));raise
