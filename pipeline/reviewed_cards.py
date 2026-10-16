"""Small, explicitly reviewed official scorecard corrections; no estimated yardages."""
import hashlib, time, urllib.request
from datetime import date
from pipeline.common import cloud_only, now, write
from pipeline.match import yardage_error


def facts(record, today=None):
    today=today or date.today()
    if not date.fromisoformat(record['reviewedAt']) <= today <= date.fromisoformat(record['expiresAt']):
        raise ValueError('Official scorecard review expired or is in the future')
    pars=record['par']; tees=record['tees']
    if len(pars) not in (9,18) or not tees or set(tees)!=set(record['totals']):
        raise ValueError('Incomplete official scorecard')
    for name,values in tees.items():
        if len(values)!=len(pars) or sum(values)!=record['totals'][name]:
            raise ValueError('Official scorecard row length or printed total mismatch: '+name)
    cards=[{'number':i+1,'par':par,'yardages':{t:ys[i] for t,ys in tees.items()}} for i,par in enumerate(pars)]
    if yardage_error(cards): raise ValueError(yardage_error(cards))
    return cards


def fetch(record, research=False):
    cloud_only()
    facts(record)
    req=urllib.request.Request(record['url'],headers={'User-Agent':'Crane-Course-Data/1.0'})
    with urllib.request.urlopen(req,timeout=45) as response:
        body=response.read(10_000_001)
    if len(body)>10_000_000: raise ValueError('Official scorecard source exceeds limit')
    digest=hashlib.sha256(body).hexdigest()
    if not research and digest != record.get('sha256'):
        raise ValueError('Official scorecard source changed or is not pinned; review required')
    return {'url':record['url'],'sha256':digest,'fetchedAt':now(),'reviewedAt':record['reviewedAt'],
            'expiresAt':record['expiresAt'],'type':'reviewed-official-scorecard','layoutName':record['layoutName']}
