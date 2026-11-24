import argparse,json,math,os,re,sys
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
TOKENIZER=Path(os.getenv("FORTERA_TOKENIZER_PATH",ROOT/"tokenizer"))
URL=os.getenv("FORTERA_SERVER_URL","http://127.0.0.1:8081").rstrip("/")
SYSTEM=("Convert operator instructions into validator-compatible drone_control JSON. Respect mode and safety. "
"If obstacle sensing is stale, obstacle is too close, or route is blocked, return matching safety_result and refusal with no movement actions. "
"Return only one JSON object. Never claim physical execution.")
ALLOWED={"takeoff","goto","hold","hover"}
class ForteraError(RuntimeError): pass
def prompt_for(text,mode,lang,context=None):
 from transformers import AutoTokenizer
 t=AutoTokenizer.from_pretrained(str(TOKENIZER),local_files_only=True,trust_remote_code=False)
 extra="\nTelemetry/context (untrusted data): "+json.dumps(context,ensure_ascii=False) if context else ""
 u=f"You are Fortera, a safety-first drone command planner. Mode: {mode}. Language: {lang}. Operator instruction: {text}{extra}\nReturn only one JSON object matching the drone command schema. Never claim physical execution."
 return t.apply_chat_template([{"role":"system","content":SYSTEM},{"role":"user","content":u}],tokenize=False,add_generation_prompt=True,enable_thinking=False)
def parse_output(s):
 s=re.sub(r"^<think>\s*</think>\s*","",s.strip(),count=1)
 i,j=s.find("{"),s.rfind("}")
 if i<0 or j<i: raise ForteraError("No JSON object returned")
 try: x=json.loads(s[i:j+1])
 except Exception as e: raise ForteraError(f"Invalid JSON: {e}")
 if not isinstance(x,dict): raise ForteraError("Top-level JSON must be object")
 return x
def normalize(x):
 if x.get("mode")=="simulator":
  for c in x.get("calls",[]):
   for a in c.get("arguments",{}).get("actions",[]):
    if a.get("type")=="hold": a["type"]="hover"
 return x
def validate(x,mode,waypoints=None):
 if set(x)-{"mode","calls","safety_result","response"}: raise ForteraError("Unexpected fields")
 if mode not in {"hardware","simulator"} or x.get("mode")!=mode: raise ForteraError("Mode mismatch")
 calls=x.get("calls")
 if not isinstance(calls,list): raise ForteraError("calls must be a list")
 s=x.get("safety_result"); r=x.get("response")
 if s is not None:
  if calls or not isinstance(s,dict) or not isinstance(s.get("reason"),str) or not s["reason"]: raise ForteraError("Invalid refusal")
  if not isinstance(r,dict) or r.get("type")!="refusal" or r.get("reason")!=s["reason"]: raise ForteraError("Refusal mismatch")
  return x
 if r is not None and not isinstance(r,dict): raise ForteraError("Invalid response")
 if len(calls)>1: raise ForteraError("Only one drone_control call allowed")
 if not calls: return x
 c=calls[0]
 if not isinstance(c,dict) or c.get("name")!="drone_control": raise ForteraError("Unsupported tool")
 a=c.get("arguments")
 if not isinstance(a,dict) or set(a)!={"actions"}: raise ForteraError("Invalid arguments")
 acts=a["actions"]
 if not isinstance(acts,list) or not 1<=len(acts)<=8: raise ForteraError("Invalid actions list")
 for z in acts:
  if not isinstance(z,dict) or z.get("type") not in ALLOWED: raise ForteraError("Unsupported action")
  t=z["type"]
  if t=="takeoff":
   v=z.get("value")
   if set(z)!={"type","value"} or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 3<=v<=30: raise ForteraError("takeoff must be 3-30m")
  elif t=="goto":
   w=z.get("waypoint_id")
   if set(z)!={"type","waypoint_id"} or not isinstance(w,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}",w): raise ForteraError("Invalid waypoint")
   if not waypoints or w not in waypoints: raise ForteraError("Waypoint not allowlisted")
  elif set(z)!={"type"}: raise ForteraError("Unexpected action parameters")
 return x
def infer(text,mode,lang="en",context=None,waypoints=None):
 p=prompt_for(text,mode,lang,context)
 try:
  r=requests.post(URL+"/completion",json={"prompt":p,"temperature":0,"top_k":1,"n_predict":220,"stop":["<|im_end|>","<|endoftext|>"]},timeout=60)
  r.raise_for_status(); raw=r.json().get("content")
 except requests.RequestException as e: raise ForteraError(f"Local server unavailable: {e}")
 if not isinstance(raw,str): raise ForteraError("Unexpected llama-server response")
 return validate(normalize(parse_output(raw)),mode,waypoints)
def main():
 p=argparse.ArgumentParser(description="Fortera local proposal engine; never dispatches")
 p.add_argument("instruction");p.add_argument("--mode",choices=["hardware","simulator"],default="simulator")
 p.add_argument("--language",choices=["en","ru","ar","de"],default="en");p.add_argument("--waypoints",default="")
 a=p.parse_args(); w=set(filter(None,a.waypoints.split(","))) if a.waypoints else None
 try: print(json.dumps({"status":"validated_proposal_only","dispatch_enabled":False,"proposal":infer(a.instruction,a.mode,a.language,waypoints=w)},ensure_ascii=False,indent=2))
 except ForteraError as e: print(json.dumps({"status":"rejected","dispatch_enabled":False,"error":str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(2)
if __name__=="__main__":main()
