import json, os, smtplib, sys
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path
import requests

BASE_URL='https://api.penpencil.co'
CLIENT_ID='5eb393ee95fab7468a79d189'
MY_SENDER_ID='69ca46ebb7bb9d3b7e522108'
STATE_FILE=Path('message_monitor_state.json')
GROUPS=[
 {'name':'Group 1','groupId':'69cb7c5e4a6bd7893a91aa22','conversationId':'69ce5c7c8a5087b50b14c482'},
 {'name':'Group 2','groupId':'69cb7c67e223436a272111c9','conversationId':'69ce5d26b3e8f731557c9116'},
 {'name':'Group 3','groupId':'69cb7c6cd6e4a400b77ebccc','conversationId':'69ce5f7b369fd47f199d51a8'},
 {'name':'Group 4','groupId':'69cb7c7036b09e3dea135a30','conversationId':'69ce60754bd6bffed97b9eeb'},
 {'name':'Group 5','groupId':'69cb7c7426c54583a30f3039','conversationId':'69ce60a65155c4ac4c289fea'},
]
PW_TOKEN=os.environ['PW_TOKEN']; ALERT_EMAIL=os.environ['ALERT_EMAIL']; GMAIL_APP_PWD=os.environ['GMAIL_APP_PWD']
HEADERS={'Authorization':f'Bearer {PW_TOKEN}','client-id':CLIENT_ID,'client-type':'WEB','origin':'https://www.pw.live','referer':'https://www.pw.live/','x-sdk-version':'0.0.28'}

def log(x): print(f"[{datetime.now().strftime('%H:%M:%S')}] {x}")
def load_state():
    if not STATE_FILE.exists(): return {'initialized':False,'seen_ids':{}}
    try:
        s=json.loads(STATE_FILE.read_text(encoding='utf-8')); s.setdefault('initialized',False); s.setdefault('seen_ids',{}); return s
    except Exception: return {'initialized':False,'seen_ids':{}}
def save_state(s):
    t=STATE_FILE.with_suffix('.tmp'); t.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8'); t.replace(STATE_FILE)
def get_messages(group):
    r=requests.get(f"{BASE_URL}/v1/conversation/{group['conversationId']}/chat",headers=HEADERS,params={'page':1,'limit':50},timeout=20)
    if r.status_code==401: raise RuntimeError('TOKEN_EXPIRED')
    r.raise_for_status(); p=r.json(); data=p.get('data',[])
    if not isinstance(data,list): raise RuntimeError(f"Unexpected response for {group['name']}")
    return data
def key(m): return (m.get('createdAt') or m.get('updatedAt') or '',str(m.get('_id','')))
def text(m):
    if isinstance(m.get('text'),str) and m['text'].strip(): return m['text'].strip()
    return {'image':'📷 Sent an image.','poll':'📊 Sent a poll.'}.get(str(m.get('type','')).lower(),'[Message with no text]')
def fmt(v):
    try:
        d=datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=5,minutes=30)))
        return d.strftime('%d %b %Y, %I:%M:%S %p IST')
    except Exception: return v or 'Unknown time'
def send_email(items):
    if not items: return
    lines=['New student message(s) detected in your PW Lakshya JEE 2027 groups.','']
    for x in items:
        lines += [f"Group: {x['group_name']}",f"Student: {x['sender_name']}",f"Time: {fmt(x['created_at'])}",f"Type: {x['type']}",'',x['text'],'','─'*60,'']
    msg=MIMEText('\n'.join(lines),'plain','utf-8'); msg['Subject']=f"🔔 Lakshya JEE 2027 — {len(items)} new student message(s)"; msg['From']=ALERT_EMAIL; msg['To']=ALERT_EMAIL
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=30) as smtp: smtp.login(ALERT_EMAIL,GMAIL_APP_PWD); smtp.send_message(msg)
    log(f'📧 Alert email sent for {len(items)} new message(s).')
def main():
    log('🚀 Student-message monitor starting'); state=load_state(); next_seen=dict(state['seen_ids']); new=[]
    for g in GROUPS:
        log(f"Checking {g['name']}..."); messages=sorted(get_messages(g),key=key); prev=set(next_seen.get(g['conversationId'],[]))
        if not state['initialized']:
            next_seen[g['conversationId']]=[str(m['_id']) for m in messages if m.get('_id')][-200:]
            log(f"  First-run baseline: {len(messages)} messages; no old-message emails."); continue
        for m in messages:
            mid=str(m.get('_id',''))
            if not mid or mid in prev or str(m.get('sender',''))==MY_SENDER_ID: continue
            new.append({'group_name':g['name'],'sender_name':m.get('senderName') or 'Unknown student','created_at':m.get('createdAt') or m.get('updatedAt') or '','type':m.get('type') or 'unknown','text':text(m)})
        ids=[str(m['_id']) for m in messages if m.get('_id')]; next_seen[g['conversationId']]=list(dict.fromkeys(list(prev)+ids))[-200:]
        log(f"  Found {sum(1 for m in new if m['group_name']==g['name'])} new student message(s).")
    state['seen_ids']=next_seen; state['initialized']=True; save_state(state)
    if new: send_email(new)
    log(f'✅ Done — {len(new)} new student message(s).')
if __name__=='__main__':
    try: main()
    except Exception as e:
        log(f'❌ Monitor failed: {e}'); sys.exit(2 if 'TOKEN_EXPIRED' in str(e) else 1)
