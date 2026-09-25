from dataclasses import dataclass
import random

from django.core.exceptions import ValidationError

from apps.question_bank.models import Question

from .models import Experiment


class SamplingValidationError(ValidationError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__([issue["message"] for issue in issues])


def question_allowed_by_history(
    question_id,
    policy,
    *,
    completed_question_ids,
    assigned_question_ids,
    active_frozen_question_ids,
):
    if policy == Experiment.CrossExperimentItemPolicy.UNRESTRICTED:
        return True
    if question_id in active_frozen_question_ids:
        return False
    if policy == Experiment.CrossExperimentItemPolicy.MIXED_REVIEW:
        return (
            question_id not in assigned_question_ids
            or question_id in completed_question_ids
        )
    return (
        question_id not in completed_question_ids
        and question_id not in assigned_question_ids
    )


@dataclass(frozen=True)
class SamplingResolution:
    candidates: list
    buckets: dict
    type_buckets: dict
    resolved_leaf_ids_by_node: dict
    review_candidates: tuple = ()
    new_candidates: tuple = ()


def _grouped_quota_flow(*, candidates, completed_ids, review_count, bucket_capacities,
                        bucket_ids_for_question, candidate_id=lambda question: question.pk,
                        rng=None):
    """Allocate unique questions while satisfying history and quota capacities."""
    source, sink = "source", "sink"
    adjacency = {}
    capacity = {}

    def add_edge(left, right, amount):
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)
        capacity[(left, right)] = amount
        capacity.setdefault((right, left), 0)

    requested = sum(bucket_capacities.values())
    add_edge(source, "group:review", review_count)
    add_edge(source, "group:new", requested - review_count)
    ordered = list(candidates)
    if rng is not None:
        rng.shuffle(ordered)
    for question in ordered:
        identity = candidate_id(question)
        question_node = f"question:{identity}"
        group = "group:review" if identity in completed_ids else "group:new"
        add_edge(group, question_node, 1)
        bucket_ids = list(bucket_ids_for_question(question))
        if rng is not None:
            rng.shuffle(bucket_ids)
        for bucket_id in bucket_ids:
            add_edge(question_node, f"bucket:{bucket_id}", 1)
    for bucket_id, amount in bucket_capacities.items():
        add_edge(f"bucket:{bucket_id}", sink, amount)

    flow = 0
    while True:
        parent = {source: None}
        queue = [source]
        for node in queue:
            for target in adjacency.get(node, []):
                if target not in parent and capacity[(node, target)] > 0:
                    parent[target] = node
                    queue.append(target)
                    if target == sink:
                        break
            if sink in parent:
                break
        if sink not in parent:
            break
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            capacity[(previous, node)] -= 1
            capacity[(node, previous)] += 1
            node = previous
        flow += 1

    allocation = {}
    for question in candidates:
        identity = candidate_id(question)
        question_node = f"question:{identity}"
        for target in adjacency.get(question_node, []):
            if target.startswith("bucket:") and capacity.get((target, question_node), 0) == 1:
                allocation[identity] = target.removeprefix("bucket:")
                break
    return flow, allocation


def _maximum_matching(rules, buckets, *, rng=None):
    slots = [
        (rule.concept_id, slot_index)
        for rule in rules
        for slot_index in range(rule.quota or 0)
    ]
    if rng is not None:
        rng.shuffle(slots)
    question_to_slot = {}
    slot_to_question = {}
    candidate_ids = {
        rule.concept_id: [question.pk for question in buckets[rule.concept_id]]
        for rule in rules
    }
    if rng is not None:
        for ids in candidate_ids.values():
            rng.shuffle(ids)

    def augment(slot, visited_questions):
        concept_id, _index = slot
        for question_id in candidate_ids[concept_id]:
            if question_id in visited_questions:
                continue
            visited_questions.add(question_id)
            previous_slot = question_to_slot.get(question_id)
            if previous_slot is None or augment(previous_slot, visited_questions):
                question_to_slot[question_id] = slot
                slot_to_question[slot] = question_id
                if (
                    previous_slot is not None
                    and slot_to_question.get(previous_slot) == question_id
                ):
                    slot_to_question.pop(previous_slot, None)
                return True
        return False

    for slot in slots:
        augment(slot, set())
    return slot_to_question


class SamplingService:
    def __init__(
        self,
        experiment,
        *,
        completed_question_ids=None,
        assigned_question_ids=None,
        active_frozen_question_ids=None,
        prepared_candidates=None,
        prepared_rules=None,
        prepared_type_quotas=None,
    ):
        self.experiment = experiment
        self.completed_question_ids = frozenset(completed_question_ids or ())
        self.assigned_question_ids = frozenset(assigned_question_ids or ())
        self.active_frozen_question_ids = frozenset(active_frozen_question_ids or ())
        self.prepared_candidates = prepared_candidates
        self.prepared_rules = prepared_rules
        self.prepared_type_quotas = prepared_type_quotas

    def _rules(self):
        if self.prepared_rules is not None:
            return list(self.prepared_rules)
        return list(
            self.experiment.sampling_kcs.select_related("concept").order_by("concept_id")
        )

    @property
    def policy(self):
        return self.experiment.cross_experiment_item_policy

    def requested_count(self):
        if self.experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT:
            return int(self.experiment.sampling_total_count or 0)
        return sum(self.experiment.sampling_kcs.values_list("quota", flat=True))

    def _filter_candidates(self, candidates):
        return [
            question for question in candidates
            if question_allowed_by_history(
                question.pk,
                self.policy,
                completed_question_ids=self.completed_question_ids,
                assigned_question_ids=self.assigned_question_ids,
                active_frozen_question_ids=self.active_frozen_question_ids,
            )
        ]

    def _issue(self, code, message, *, kc_id=None, field=None, **metadata):
        issue = {
            "code": code,
            "message": message,
            "class_id": None,
            "kc_id": kc_id,
            "field": field,
        }
        issue.update(metadata)
        return issue

    @staticmethod
    def _question_matches(question, concept_id, match_scope):
        links = list(question.concept_links.all())
        if match_scope == Experiment.MatchScope.PRIMARY:
            return any(link.is_primary and link.concept_id == concept_id for link in links)
        return any(link.concept_id == concept_id for link in links)

    def _type_quota_map(self):
        if self.prepared_type_quotas is not None:
            return dict(self.prepared_type_quotas)
        return {
            row.question_type: row.amount
            for row in self.experiment.question_quotas.filter(amount__gt=0)
        }

    def _history_flow_inputs(self, resolution, rules):
        if self.experiment.sampling_mode == Experiment.SamplingMode.KC_QUOTA:
            capacities = {str(rule.concept_id): rule.quota for rule in rules}
            scopes = {rule.concept_id: rule.match_scope for rule in rules}

            def bucket_ids(question):
                return [
                    str(rule.concept_id) for rule in rules
                    if self._question_matches(question, rule.concept_id, scopes[rule.concept_id])
                ]
        else:
            type_quotas = self._type_quota_map()
            capacities = (
                {str(question_type): amount for question_type, amount in type_quotas.items()}
                if type_quotas
                else {"all": self.requested_count()}
            )

            def bucket_ids(question):
                return [str(question.question_type)] if type_quotas else ["all"]
        return capacities, bucket_ids

    def _validate_history_constraints(self, resolution, rules):
        if self.policy != Experiment.CrossExperimentItemPolicy.MIXED_REVIEW:
            return
        requested = self.requested_count()
        review_count = int(self.experiment.review_count or 0)
        new_count = requested - review_count
        issues = []
        if len(resolution.review_candidates) < review_count:
            issues.append(self._issue(
                "REVIEW_POOL_INSUFFICIENT",
                f"复测题需要 {review_count} 题，可用 {len(resolution.review_candidates)} 题。",
                field="review_count",
                requested=review_count,
                available=len(resolution.review_candidates),
            ))
        if len(resolution.new_candidates) < new_count:
            issues.append(self._issue(
                "NEW_POOL_INSUFFICIENT",
                f"新题需要 {new_count} 题，可用 {len(resolution.new_candidates)} 题。",
                field="review_count",
                requested=new_count,
                available=len(resolution.new_candidates),
            ))
        if issues:
            raise SamplingValidationError(issues)
        capacities, bucket_ids = self._history_flow_inputs(resolution, rules)
        maximum, _allocation = _grouped_quota_flow(
            candidates=resolution.candidates,
            completed_ids=self.completed_question_ids,
            review_count=review_count,
            bucket_capacities=capacities,
            bucket_ids_for_question=bucket_ids,
        )
        if maximum < requested:
            raise SamplingValidationError([self._issue(
                "GLOBAL_MATCHING_INSUFFICIENT",
                f"题目历史构成与现有配额无法同时满足：需要 {requested} 题，最多可分配 {maximum} 题。",
                field="review_count",
                requested=requested,
                maximum_assignable=maximum,
            )])

    @classmethod
    def total_count_candidates(cls, *, question_bank_id, concept_ids, match_scope):
        questions = list(
            Question.objects.filter(
                bank_id=question_bank_id,
                status=Question.Status.ACTIVE,
            )
            .prefetch_related("concept_links")
            .order_by("sequence", "pk")
        )
        if not concept_ids:
            return questions
        return [
            question
            for question in questions
            if any(
                cls._question_matches(question, concept_id, match_scope)
                for concept_id in concept_ids
            )
        ]

    def resolve(self, *, validate_inventory=True):
        mode = self.experiment.sampling_mode
        rules = self._rules()
        if mode == Experiment.SamplingMode.TOTAL_COUNT:
            if not self.experiment.sampling_total_count:
                raise SamplingValidationError([
                    self._issue(
                        "TOTAL_COUNT_CONFIGURATION_REQUIRED",
                        "按总题数抽题必须设置总题数。",
                        field="sampling_total_count",
                    )
                ])
            if any(rule.quota is not None for rule in rules):
                raise SamplingValidationError([
                    self._issue("TOTAL_COUNT_HAS_QUOTA", "总题数模式不能设置知识点配额。", field="sampling_kcs")
                ])
            type_quotas = self._type_quota_map()
            if type_quotas and sum(type_quotas.values()) != self.experiment.sampling_total_count:
                raise SamplingValidationError([
                    self._issue(
                        "QUESTION_TYPE_QUOTA_TOTAL_MISMATCH",
                        "题型构成数量合计必须严格等于每位参与者题目数。",
                        field="sampling_type_quotas",
                    )
                ])
        elif mode == Experiment.SamplingMode.KC_QUOTA:
            if self.experiment.sampling_total_count is not None or not rules or any(not rule.quota for rule in rules):
                raise SamplingValidationError([
                    self._issue("KC_QUOTA_CONFIGURATION_REQUIRED", "知识点配额模式必须为每个节点设置正整数配额。", field="sampling_kcs")
                ])
        else:
            raise SamplingValidationError([
                self._issue("SAMPLING_MODE_REQUIRED", "新抽题配置必须选择抽题模式。", field="sampling_mode")
            ])

        resolved = {rule.concept_id: [rule.concept_id] for rule in rules}
        if mode == Experiment.SamplingMode.TOTAL_COUNT:
            scope = self.experiment.sampling_match_scope
            candidates = (
                list(self.prepared_candidates)
                if self.prepared_candidates is not None
                else self.total_count_candidates(
                    question_bank_id=self.experiment.question_bank_id,
                    concept_ids=[rule.concept_id for rule in rules],
                    match_scope=scope,
                )
            )
            candidates = self._filter_candidates(candidates)
            type_buckets = {
                question_type: [
                    question
                    for question in candidates
                    if question.question_type == question_type
                ]
                for question_type in Question.Type.values
            }
            type_quotas = self._type_quota_map()
            type_issues = [
                self._issue(
                    "QUESTION_TYPE_QUOTA_INSUFFICIENT",
                    f"题型“{Question.Type(question_type).label}”需要 {requested} 题，可用 {len(type_buckets[question_type])} 题。",
                    field="sampling_type_quotas",
                    question_type=question_type,
                    requested=requested,
                    available=len(type_buckets[question_type]),
                )
                for question_type, requested in type_quotas.items()
                if len(type_buckets[question_type]) < requested
            ]
            if validate_inventory and type_issues:
                raise SamplingValidationError(type_issues)
            if (
                validate_inventory
                and not type_quotas
                and self.policy != Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
                and len(candidates) < self.experiment.sampling_total_count
            ):
                raise SamplingValidationError([
                    self._issue(
                        "TOTAL_COUNT_INSUFFICIENT",
                        f"当前题目范围需要 {self.experiment.sampling_total_count} 题，可用 {len(candidates)} 题。",
                        field="sampling_total_count",
                    )
                ])
            resolution = SamplingResolution(
                candidates, {}, type_buckets, resolved,
                tuple(question for question in candidates if question.pk in self.completed_question_ids),
                tuple(question for question in candidates if question.pk not in self.completed_question_ids),
            )
            if validate_inventory:
                self._validate_history_constraints(resolution, rules)
            return resolution

        questions = (
            list(self.prepared_candidates)
            if self.prepared_candidates is not None
            else list(
                Question.objects.filter(
                    bank_id=self.experiment.question_bank_id,
                    status=Question.Status.ACTIVE,
                )
                .prefetch_related("concept_links")
                .order_by("sequence", "pk")
            )
        )
        questions = self._filter_candidates(questions)
        buckets = {
            rule.concept_id: [
                question for question in questions
                if self._question_matches(question, rule.concept_id, rule.match_scope)
            ]
            for rule in rules
        }
        issues = [
            self._issue(
                "KC_QUOTA_INSUFFICIENT",
                f"知识点“{rule.concept.name}”需要 {rule.quota} 题，可用 {len(buckets[rule.concept_id])} 题。",
                kc_id=rule.concept_id,
                kc_code=rule.concept.code,
                field="sampling_kcs",
            )
            for rule in rules
            if len(buckets[rule.concept_id]) < rule.quota
        ]
        if validate_inventory and issues:
            raise SamplingValidationError(issues)
        if validate_inventory:
            matching = _maximum_matching(rules, buckets)
            requested = sum(rule.quota for rule in rules)
            if len(matching) < requested:
                raise SamplingValidationError([
                    self._issue(
                        "KC_QUOTA_GLOBAL_INSUFFICIENT",
                        f"配额合计需要 {requested} 题，但重叠候选最多只能无重复分配 {len(matching)} 题。",
                        field="sampling_kcs",
                        requested=requested,
                        maximum_assignable=len(matching),
                        overlapping_kc_codes=[rule.concept.code for rule in rules],
                    )
                ])
        candidates = sorted(
            {question.pk: question for bucket in buckets.values() for question in bucket}.values(),
            key=lambda question: (question.sequence, question.pk),
        )
        resolution = SamplingResolution(
            candidates, buckets, {}, resolved,
            tuple(question for question in candidates if question.pk in self.completed_question_ids),
            tuple(question for question in candidates if question.pk not in self.completed_question_ids),
        )
        if validate_inventory:
            self._validate_history_constraints(resolution, rules)
        return resolution

    def select_with_attribution(self, sampling_seed):
        resolution = self.resolve()
        rng = random.Random(int(sampling_seed[:16], 16))
        if self.policy == Experiment.CrossExperimentItemPolicy.MIXED_REVIEW:
            rules = self._rules()
            capacities, bucket_ids = self._history_flow_inputs(resolution, rules)
            maximum, allocation = _grouped_quota_flow(
                candidates=resolution.candidates,
                completed_ids=self.completed_question_ids,
                review_count=self.experiment.review_count,
                bucket_capacities=capacities,
                bucket_ids_for_question=bucket_ids,
                rng=rng,
            )
            if maximum < self.requested_count():
                raise SamplingValidationError([self._issue(
                    "GLOBAL_MATCHING_INSUFFICIENT",
                    "题目历史构成与现有配额无法同时满足。",
                    field="review_count",
                    requested=self.requested_count(),
                    maximum_assignable=maximum,
                )])
            questions = {question.pk: question for question in resolution.candidates}
            scope_by_concept = {str(rule.concept_id): rule.match_scope for rule in rules}
            selected = []
            for question_id, bucket_id in allocation.items():
                question = questions[question_id]
                selected.append({
                    "question": question,
                    "quota_kc_id": (
                        int(bucket_id)
                        if self.experiment.sampling_mode == Experiment.SamplingMode.KC_QUOTA
                        else None
                    ),
                    "quota_question_type": (
                        question.question_type
                        if self.experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
                        and bucket_id != "all"
                        else None
                    ),
                    "match_scope": (
                        scope_by_concept[bucket_id]
                        if self.experiment.sampling_mode == Experiment.SamplingMode.KC_QUOTA
                        else self.experiment.sampling_match_scope
                    ),
                    "history_role": (
                        "review" if question_id in self.completed_question_ids else "new"
                    ),
                    "cross_experiment_item_policy": self.policy,
                })
            return sorted(selected, key=lambda item: (item["question"].sequence, item["question"].pk))
        if self.experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT:
            type_quotas = self._type_quota_map()
            if type_quotas:
                selected = [
                    question
                    for question_type in Question.Type.values
                    for question in rng.sample(
                        resolution.type_buckets[question_type],
                        type_quotas.get(question_type, 0),
                    )
                ]
            else:
                selected = rng.sample(resolution.candidates, self.experiment.sampling_total_count)
            return [
                {
                    "question": question,
                    "quota_kc_id": None,
                    "quota_question_type": question.question_type if type_quotas else None,
                    "match_scope": self.experiment.sampling_match_scope,
                    "history_role": (
                        "new"
                        if self.policy == Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS
                        else "unrestricted"
                    ),
                    "cross_experiment_item_policy": self.policy,
                }
                for question in sorted(selected, key=lambda item: (item.sequence, item.pk))
            ]
        rules = self._rules()
        matching = _maximum_matching(rules, resolution.buckets, rng=rng)
        questions = {question.pk: question for question in resolution.candidates}
        scope_by_concept = {rule.concept_id: rule.match_scope for rule in rules}
        selected = [
            {
                "question": questions[question_id],
                "quota_kc_id": concept_id,
                "quota_question_type": None,
                "match_scope": scope_by_concept[concept_id],
                "history_role": (
                    "new"
                    if self.policy == Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS
                    else "unrestricted"
                ),
                "cross_experiment_item_policy": self.policy,
            }
            for (concept_id, _slot), question_id in matching.items()
        ]
        return sorted(selected, key=lambda item: (item["question"].sequence, item["question"].pk))

    def select(self, sampling_seed):
        return [item["question"] for item in self.select_with_attribution(sampling_seed)]
