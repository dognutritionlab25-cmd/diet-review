"""Pure, deterministic nutrition calculations. No Streamlit or network imports.
Stage 1 deliberately preserves effective retention and per-app standards.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

ENGINE_VERSION = '1.1.0-fruit-raw'
_DATA = json.loads(Path(__file__).with_name('catalog.json').read_text())

def canonical_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()

FOOD_DB_VERSION = 'food-' + digest({k:_DATA[k] for k in ['db_data','omega_db','amino_db','amino_name_map','FRUIT_RAW_ITEMS']})[:16]
ENGINE_SOURCE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_POLICY = {
    'version':'stage1-fruit-raw-v1',
    'energy':'DB kcal; no cooking correction',
    'retention':_DATA['RETENTION'],
    'cooking_yield':_DATA['COOKING_YIELD'],
    'omega3_retention':'legacy_mineral_fallback_preserved',
    'precooked':_DATA['PRECOOKED_ITEMS'],
    'raw_fruit':_DATA['FRUIT_RAW_ITEMS'],
    'missing_basic_nutrients':'registered subtotal; coverage marks unavailable values',
    'yield_organ':'use_defined_organ_entry',
    'standards':{
        'calculator':_DATA['aafco_standards'],
        'review':{**_DATA['aafco_standards'],'요오드(mcg)':{'min':220,'max':1400}},
    },
}
CALCULATION_POLICY_VERSION = 'policy-' + digest(_POLICY)[:16]
UNRESOLVED_NUTRITION_POLICY = [
    {'id':'iodine_thresholds','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'Preserve calculator 250/2750 and review 220/1400.'},
    {'id':'cooking_retention','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'No coefficient changes; preserve effective omega3 mineral fallback pending approval.'},
    {'id':'kelp_modes','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'Calculator raw only; review raw and cooked. No new dosing policy.'},
    {'id':'ratio_and_amino_standards','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'Existing UI thresholds retained, no new nutrition recommendations.'},
    {'id':'omega_database_and_oil_energy','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'No DB correction or estimated oil calories. Missing and inconsistent data flagged.'},
    {'id':'precooked_sardine_and_food_categories','status':'UNRESOLVED_NUTRITION_POLICY','behavior':'Existing exceptions and classifications retained.'},
]

def catalog_data(): return deepcopy(_DATA)
def policy_data(): return deepcopy(_POLICY)
def standards(profile):
    if profile not in _POLICY['standards']:raise ValueError('Unknown profile')
    return deepcopy(_POLICY['standards'][profile])

def number(value,label='number'):
    if isinstance(value,bool):raise ValueError(label+': boolean is not a number')
    try:n=float(value)
    except (TypeError,ValueError):raise ValueError(label+': invalid numeric value') from None
    if not math.isfinite(n) or n<0:raise ValueError(label+': must be finite and nonnegative')
    return n

def retention(nutrient,method):
    if method not in _DATA['RETENTION']:raise ValueError('Unknown cooking method')
    r=_DATA['RETENTION'][method]
    if nutrient in ('단백질(g)','지방(g)'):group='단백질'
    elif nutrient in ('비타민A(IU)','비타민D(IU)','비타민E(IU)'):group=nutrient.split('(')[0]
    else:group='미네랄' # Preserves effective original omega3 behavior, explicitly unresolved.
    return sum(r[group])/200

def energy_requirements(weight, activity):
    weight=number(weight, "weight");activity=number(activity, "activity")
    if weight<=0 or activity<=0:raise ValueError("Weight/activity must be positive")
    rer=70*(weight**.75)
    return rer, rer*activity

def make_request(amounts, *, cooked=False, method='삶기', weight=3.0, activity=1.6,
                 kelp=0, calcium=0, epa=0, dha=0, omega_unit='mg',
                 actual_weights=None, supplements=None, original_fields=None, excluded=None):
    if omega_unit not in ('mg','g'):raise ValueError('Unsupported omega unit')
    return {'schema_version':'diet-input-v1','weight_kg':weight,'activity_factor':activity,
            'mode':'cooked' if cooked else 'raw','method':method if cooked else '생식',
            'items':[{'name':n,'grams':g,'weight_basis':'cooked' if n in _DATA['PRECOOKED_ITEMS'] else 'raw',
                      'actual_cooked_g':(actual_weights or {}).get(n)} for n,g in amounts.items()],
            'supplement_totals':{'iodine_mcg':kelp,'calcium_mg':calcium,
                                 'epa':epa,'dha':dha,'omega_unit':omega_unit},
            'supplement_inputs':deepcopy(supplements or {}),
            'original_fields':deepcopy(original_fields or {}),'excluded':deepcopy(excluded or [])}

def calculate(request,profile='review'):
    req=deepcopy(request)
    if req.get('schema_version')!='diet-input-v1':raise ValueError('Unknown input schema')
    std=standards(profile)
    weight=number(req['weight_kg'],'weight'); activity=number(req['activity_factor'],'activity')
    if weight<=0 or activity<=0:raise ValueError('Weight/activity must be positive')
    if req['mode'] not in ('raw','cooked'):raise ValueError('Unknown diet mode')
    cooked=req['mode']=='cooked'; method=req['method']
    if cooked and method not in _DATA['RETENTION']:raise ValueError('Unknown cooking method')
    foods={x['재료명']:x for x in _DATA['db_data']}
    raw_fruits=set(_DATA['FRUIT_RAW_ITEMS'])
    nutrients={k:0.0 for k in std}; kcal=0.; grams_total=0.; cooked_total=0.
    mass={'actual_bone':0.,'muscle_meat':0.,'organ':0.,'veggie':0.}
    aa={k:0. for k in next(iter(_DATA['amino_db'].values()))}
    omega6=0.;omega3=0.;contributions=[];missing_omega=[];missing_aa=[]
    data_warnings=[]; missing_nutrients={k:[] for k in std}
    for item in req['items']:
        name=item['name']; g=number(item['grams'],'grams')
        if name not in foods:raise ValueError('Unknown food: '+name)
        row=foods[name]; cat=row['category']; precooked=name in _DATA['PRECOOKED_ITEMS']; raw_fruit=name in raw_fruits
        if cooked and cat=='bone':raise ValueError('Bone item not allowed in cooked mode')
        expected_basis='cooked' if precooked else 'raw'
        if item.get('weight_basis')!=expected_basis:raise ValueError('Weight basis incompatible with legacy food profile')
        actual=item.get('actual_cooked_g')
        if actual is not None:actual=number(actual,'actual cooked grams')
        grams_total+=g
        predicted=g if not cooked or precooked or raw_fruit else round(g*_DATA['COOKING_YIELD'][method][cat])
        cooked_total+=actual if cooked and actual is not None and not precooked and not raw_fruit else predicted
        if g<=0:continue
        factor=g/100; nk={}; kcal+=row['칼로리']*factor
        for key in nutrients:
            field=key if key in row else key.split('(')[0]
            source_value=row.get(field)
            if source_value is None:
                missing_nutrients[key].append(name); nk[key]=None
                continue
            value=source_value*factor
            if cooked and cat!='veggie' and not precooked:value*=retention(key,method)
            nutrients[key]+=value; nk[key]=value
        if cat=='bone':
            mass['actual_bone']+=g*row['bone_pct'];mass['muscle_meat']+=g*(1-row['bone_pct'])
        else:mass[{'meat':'muscle_meat','organ':'organ','veggie':'veggie'}[cat]]+=g
        aa_key=_DATA['amino_name_map'].get(name)
        if aa_key and aa_key in _DATA['amino_db']:
            rf=retention('단백질(g)',method) if cooked and cat!='veggie' and not precooked else 1.
            for key,v in _DATA['amino_db'][aa_key].items():aa[key]+=v*factor*rf
        else:missing_aa.append(name)
        if name in _DATA['omega_db']:
            o6,o3,*_=_DATA['omega_db'][name]
            rf=retention('오메가3',method) if cooked and cat!='veggie' and not precooked else 1.
            omega6+=o6*factor;omega3+=o3*factor*rf
            if o6+o3>row['지방']:data_warnings.append({'food':name,'code':'FATTY_ACIDS_EXCEED_TOTAL_FAT'})
        else:missing_omega.append(name)
        contributions.append({'name':name,'grams':g,'kcal':row['칼로리']*factor,'nutrients':nk})
    supp=req['supplement_totals'];unit=supp['omega_unit']
    if unit not in ('mg','g'):raise ValueError('Unsupported omega unit')
    iodine=number(supp['iodine_mcg']);calcium=number(supp['calcium_mg'])
    epa=number(supp['epa'])/(1000 if unit=='mg' else 1)
    dha=number(supp['dha'])/(1000 if unit=='mg' else 1)
    # Input adapters preserve each app's supported modes; engine never invents dosage.
    nutrients['요오드(mcg)']+=iodine;nutrients['칼슘(mg)']+=calcium
    omega_food=omega3;omega3+=epa+dha
    rer,der=energy_requirements(weight,activity)
    safe_ratio=lambda a,b:a/b if b>0 else None
    return {'schema_version':'nutrition-result-v1','engine_version':ENGINE_VERSION,'engine_source_hash':ENGINE_SOURCE_HASH,
            'food_db_version':FOOD_DB_VERSION,'calculation_policy_version':CALCULATION_POLICY_VERSION,
            'profile':profile,'input_hash':digest(req),'kcal':kcal,'nutrients':nutrients,
            'per_1000kcal':{k:v/kcal*1000 if kcal>0 else None for k,v in nutrients.items()},
            'mass':mass,'mass_percent':{k:v/grams_total*100 if grams_total>0 else 0 for k,v in mass.items()},'input_grams':grams_total,'cooked_grams':cooked_total,'rer':rer,'der':der,
            'amino':aa,'amino_per_1000kcal':{k:v/kcal*1000 if kcal>0 else None for k,v in aa.items()},'phenylalanine_plus_tryptophan':aa['페닐알라닌']+aa['트립토판'],'bcaa':sum(aa[k] for k in ['류신','이소류신','발린']),
            'omega6':omega6,'omega3':omega3,'omega3_food':omega_food,'epa_supplement_g':epa,'dha_supplement_g':dha,
            'ratios':{'ca_p':safe_ratio(nutrients['칼슘(mg)'],nutrients['인(mg)']),
                      'zn_cu':safe_ratio(nutrients['아연(mg)'],nutrients['구리(mg)']),
                      'omega6_3':safe_ratio(omega6,omega3)},
            'contributions':contributions,'supplement_contributions':{'iodine_mcg':iodine,'calcium_mg':calcium,'epa_g':epa,'dha_g':dha},
            'excluded':req.get('excluded',[]),'coverage':dict(
                {'amino_missing':missing_aa,'omega_missing':missing_omega},
                **({'nutrient_missing':{k:v for k,v in missing_nutrients.items() if v}}
                   if any(missing_nutrients.values()) else {})),
            'data_warnings':data_warnings,'policy_status':deepcopy(UNRESOLVED_NUTRITION_POLICY)}

def basic_judgments(result,profile=None,reference=None):
    """Existing 13-nutrient comparisons, not a new clinical rules engine."""
    out={}
    for k,std in (reference if reference is not None else standards(profile)).items():
        v=result['per_1000kcal'][k]
        missing=result.get('coverage',{}).get('nutrient_missing',{}).get(k,[])
        out[k]='unavailable' if v is None or missing else 'low' if v<std['min'] else 'high' if std['max'] is not None and v>std['max'] else 'within'
    return out
