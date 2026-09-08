"""Lossless input/result envelopes; optional Sheets column, no schema mutation."""
from copy import deepcopy
import json
import re
from .engine import calculate, make_request, digest, canonical_json, policy_data

SNAPSHOT_COLUMN='입력계산스냅샷'
class SnapshotError(ValueError):pass

def create_snapshot(request,result,original_input):
    if result['input_hash']!=digest(request):raise SnapshotError('Input/result mismatch')
    body={'schema_version':'diet-snapshot-v1','original_input':deepcopy(original_input),
          'request':deepcopy(request),'result':deepcopy(result),'policy_snapshot':policy_data()}
    return {**body,'snapshot_hash':digest(body)}

def dumps_snapshot(snapshot):
    validate_snapshot(snapshot)
    return canonical_json(snapshot)

def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get('schema_version')!='diet-snapshot-v1':raise SnapshotError('Unknown snapshot schema')
    body={k:v for k,v in snapshot.items() if k!='snapshot_hash'}
    try:
        if snapshot.get('snapshot_hash')!=digest(body):raise SnapshotError('Snapshot checksum mismatch')
        if snapshot['result']['input_hash']!=digest(snapshot['request']):raise SnapshotError('Input checksum mismatch')
        for key in ['food_db_version','calculation_policy_version','engine_version']:
            if not snapshot['result'][key]:raise SnapshotError('Missing version')
    except (KeyError,TypeError) as e:raise SnapshotError('Incomplete snapshot') from e
    return deepcopy(snapshot)

def loads_snapshot(text):
    try:return validate_snapshot(json.loads(text))
    except (json.JSONDecodeError,TypeError) as e:raise SnapshotError('Invalid snapshot JSON') from e

def parse_material_string(text):
    """Legacy format: protect commas inside parentheses (four known DB names).
    Original text is retained in snapshots. Ambiguous free text is NOT inferred.
    """
    parts=[];start=0;depth=0
    for i,char in enumerate(str(text or '')):
        if char=='(':depth+=1
        elif char==')':depth=max(0,depth-1)
        elif char==',' and depth==0:parts.append(text[start:i]);start=i+1
    parts.append(str(text or '')[start:]);out={}
    for part in parts:
        if ':' not in part:continue
        name,amount=part.rsplit(':',1)
        if name.strip() in out:raise SnapshotError('Duplicate legacy material name; manual review required')
        out[name.strip()]=amount.strip()
    return out

def grams(value):
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)g\s*',str(value))
    return float(match.group(1)) if match else None

def review_analysis(row,food_names):
    """Default to stored result, never silently recalculate a valid snapshot."""
    payload=row.get(SNAPSHOT_COLUMN,'')
    if payload and str(payload).strip():
        snap=loads_snapshot(payload)
        return snap['result'],[],snap
    parsed=parse_material_string(row.get('선택재료',''))
    amounts={};excluded=[]
    for name,value in parsed.items():
        g=grams(value)
        if name in food_names and g is not None:amounts[name]=g
        else:excluded.append({'name':name,'amount_text':value,'reason':'legacy_unmapped'})
    cooked=row.get('식단종류')=='화식'
    req=make_request(amounts,cooked=cooked,method=row.get('조리방법') or '삶기',
                     weight=row.get('체중(kg)') or 3,activity=row.get('활동계수') or 1.6,
                     original_fields=row,excluded=excluded)
    result=calculate(req,'review')
    gaps=['LEGACY_NO_SNAPSHOT','KELP_AMOUNT_NOT_RECORDED']
    if cooked:gaps.append('CALCIUM_SUPPLEMENT_NOT_RECORDED')
    result['input_gaps']=gaps
    return result,gaps,None
