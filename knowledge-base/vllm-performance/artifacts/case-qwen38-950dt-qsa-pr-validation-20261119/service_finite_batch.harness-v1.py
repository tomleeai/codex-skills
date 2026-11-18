"""Finite correctness batches: fresh random token prefixes, 128K+1024, C=1/4."""
import argparse
import os
import concurrent.futures
import hashlib
import json
import random
import re
import time
import urllib.request
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True);p.add_argument('--tag',required=True);a=p.parse_args()
root=Path(a.output_dir);root.mkdir(parents=True,exist_ok=True)
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
P=os.environ['P_URL'];D=os.environ['D_URL'];PROXY=os.environ['PROXY_URL'];MODEL='qwen3.8-flash-next'


def request(url,data=None,timeout=20):
    req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with http.open(req,timeout=timeout) as response:return response.read().decode()


def save(name,data):
    path=root/name;temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data,indent=2));temporary.replace(path)


def metrics(stage):
    result={}
    for role,url in [('p',P),('d',D)]:
        text=request(url+'/metrics');(root/(stage+'-'+role+'.prom')).write_text(text)
        values={}
        for line in text.splitlines():
            if line.startswith('#'):continue
            match=re.match(r'^(\S+)\s+([-+\d.eE]+)(?:\s|$)',line)
            if match and any(word in match[1] for word in ['prefix_cache','draft','accepted','tokens_total','preempt']):
                values[match[1]]=float(match[2])
        result[role]=values
    return result


pool=json.loads(request(P+'/tokenize',{'model':MODEL,'prompt':' amber blue copper dawn earth forest green harbor island jade king lemon mountain north ocean paper quiet river silver tree union valley water yellow zebra '+
    ' apple bread cloud dream energy field garden house idea journey kite light moon night orange planet question road stone time universe voice wind year zero ', 'add_special_tokens':False}))['tokens']
pool=list(dict.fromkeys(pool));assert len(pool)>32,pool
protocol={'tag':a.tag,'input_tokens':131072,'output_tokens':1024,'concurrencies':[1,4],'requests_per_concurrency':4,
    'prefix_protocol':'independent random token IDs from the first block, seed per request; P cache hits must remain zero; P-to-D KV delivery reported separately',
    'token_pool':pool,'cases':[],'state':'running'}
save(a.tag+'-protocol.json',protocol)


def one_request(case_index,index):
    seed=21979000+case_index*1000+index
    rng=random.Random(seed);prompt=[rng.choice(pool) for _ in range(131072)]
    fingerprint=hashlib.sha256(json.dumps(prompt,separators=(',',':')).encode()).hexdigest()
    started=time.monotonic();item={'index':index,'seed':seed,'input_sha256':fingerprint,'first_tokens':prompt[:16]}
    try:
        response=json.loads(request(PROXY+'/v1/completions',{'model':MODEL,'prompt':prompt,'add_special_tokens':False,
            'max_tokens':1024,'ignore_eos':True,'temperature':0,'stream':False},timeout=900))
        usage=response.get('usage',{});item.update(usage=usage,finish_reason=response['choices'][0].get('finish_reason'))
        assert usage.get('prompt_tokens')==131072,usage
        assert usage.get('completion_tokens')==1024,usage
        assert usage.get('prompt_tokens_details',{}).get('cached_tokens',0)==0,usage
        item['status']='PASS'
    except Exception as error:item.update(status='FAIL',error=repr(error))
    item['elapsed_seconds']=time.monotonic()-started
    save(a.tag+f'-c{[1,4][case_index]}-request{index:02d}.json',item)
    return item


for case_index,concurrency in enumerate([1,4]):
    count=4*concurrency;before=metrics(a.tag+f'-c{concurrency}-before');start=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        items=list(executor.map(lambda index:one_request(case_index,index),range(count)))
    elapsed=time.monotonic()-start
    # Give engine statistics publication a bounded chance to catch up.
    time.sleep(2);after=metrics(a.tag+f'-c{concurrency}-after')
    delta={role:{key:value-before[role].get(key,0) for key,value in after[role].items()} for role in ['p','d']}
    prefix_hits={key:value for key,value in delta['p'].items() if 'prefix_cache_hits_total' in key}
    failures=[item for item in items if item['status']!='PASS']
    case={'concurrency':concurrency,'requests':count,'successes':count-len(failures),'elapsed_seconds':elapsed,
          'requests_detail':items,'selected_metric_changes':delta,'p_prefix_hits':prefix_hits,
          'p_prefix_check':'PASS' if prefix_hits and all(value==0 for value in prefix_hits.values()) else 'UNKNOWN' if not prefix_hits else 'FAIL'}
    case['status']='PASS' if not failures and case['p_prefix_check']=='PASS' else 'FAIL'
    save(a.tag+f'-c{concurrency}-result.json',case);protocol['cases'].append(case);save(a.tag+'-protocol.json',protocol)
    print(json.dumps({key:case[key] for key in ['concurrency','requests','successes','elapsed_seconds','p_prefix_check','status']}),flush=True)
    if failures:break
protocol['state']='PASS' if len(protocol['cases'])==2 and all(case['status']=='PASS' for case in protocol['cases']) else 'FAIL'
save(a.tag+'-protocol.json',protocol)
raise SystemExit(int(protocol['state']!='PASS'))
