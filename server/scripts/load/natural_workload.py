"""Frozen natural school workload shared by browser witnesses and protocol clients."""
import random

NATURAL_WORKLOAD = 'natural-180-v1'
CONFIDENCE_ONLY = 'confidence-only-v1'
SPEED_BOUNDS = {'fast': (35,55), 'medium': (45,75), 'slow': (65,95)}


def build_workload(fixture, seed=20261013):
    n, names = fixture['student_count'], fixture['usernames']
    if n not in (2,10,80,180) or len(names) != n or len(set(names)) != n or fixture['item_count'] !=20:
        raise ValueError('Natural workload requires distinct 2/10/80/180 students and20 items')
    rng=random.Random(seed)
    raw=[n*.4,n*.45,n*.15]
    counts=[int(v) for v in raw]
    for i in sorted(range(3),key=lambda i: (raw[i]-counts[i],-i),reverse=True)[:n-sum(counts)]: counts[i]+=1
    speeds=[speed for speed,count in zip(SPEED_BOUNDS,counts) for _ in range(count)]
    rng.shuffle(speeds)
    selected={field:set(rng.sample(range(n),1 if n == 2 else round(n*fraction))) for field,fraction in
        [('modify_items',.3),('excursion_items',.2),('optional_tool_items',.2)]}
    students=[]
    for i,name in enumerate(names):
        low,high=SPEED_BOUNDS[speeds[i]]
        students.append({'username':name,'role':'protocol' if name in fixture.get('protocol_usernames', names[2:]) else 'browser',
            'arrival_offset_seconds':round(rng.uniform(0,60),3),'speed_stratum':speeds[i],
            'item_dwell_seconds':[round(rng.uniform(low,high),3) for _ in range(20)],
            **{field:sorted(rng.sample(range(19 if field=='excursion_items' else 20),2))
                if i in chosen else[] for field,chosen in selected.items()}})
    plan = {'workload_version':NATURAL_WORKLOAD,'total_students':n,'item_count':20,
        'task_id':fixture['task_id'],'seed':seed,'students':students}
    if fixture.get('scenario') == CONFIDENCE_ONLY:
        version = fixture.get('confidence_scale_version', 'v2')
        if version not in ('v1', 'v2'):
            raise ValueError('Unsupported confidence scale')
        scale = {'minimum':0,'maximum':100,'step':1 if version == 'v2' else 10}
        plan.update(scenario=CONFIDENCE_ONLY, synthetic_confidence=True,
            confidence_scale_version=version, confidence_scale=scale)
        confidence_rng = random.Random(f'{seed}:{CONFIDENCE_ONLY}')
        legal_values = list(range(scale['minimum'], scale['maximum'] + 1, scale['step']))
        for student in students:
            skips = set(confidence_rng.sample(range(20), 4))
            student.update(optional_tool_items=[], confidence_actions=['skip' if i in skips else 'fill' for i in range(20)],
                confidence_values=[None if i in skips else confidence_rng.choice(legal_values) for i in range(20)],
                confidence_think_seconds=[round(confidence_rng.uniform(1, 3), 3) for _ in range(20)])
    return plan


def validate_workload(fixture, plan):
    if plan != build_workload(fixture,plan.get('seed')):
        raise ValueError('Frozen natural plan differs from deterministic fixture/seed material')
    return plan


def visit_indices(student, item_count=20):
    """Authorized entry order: excursion next/back precedes current-item submission."""
    order=[0]
    for index in range(item_count):
        if index in student['excursion_items']: order.extend([index+1,index])
        if index+1<item_count: order.append(index+1)
    return order



def expected_input_counts(student, kinds):
    per_item = [(2 if kind in ('fill_blank', 'short_answer') else 1) + (index in student['modify_items'])
        for index, kind in enumerate(kinds)]
    return {'transitions': sum(per_item), 'text_operations': sum(count for kind, count in zip(kinds, per_item)
        if kind in ('fill_blank', 'short_answer')), 'per_item': per_item}


def confidence_decision(student, index, contract, head):
    action, value = student['confidence_actions'][index], student['confidence_values'][index]
    if action == 'skip' and value is None:
        return {'status':'skipped','answer_event_id':head}
    if action != 'fill' or isinstance(value, bool) or not isinstance(value, int) or not (
            contract['minimum'] <= value <= contract['maximum']) or (value - contract['minimum']) % contract['step']:
        raise ValueError('Frozen confidence value differs from legal scale/action')
    return {'status':'committed','probability':value,'answer_event_id':head}
