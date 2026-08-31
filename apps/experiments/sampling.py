from dataclasses import dataclass
import random

from django.core.exceptions import ValidationError

from apps.question_bank.models import Question

from .models import Experiment


class SamplingValidationError(ValidationError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__([issue["message"] for issue in issues])


@dataclass(frozen=True)
class SamplingResolution:
    candidates: list
    buckets: dict
    type_buckets: dict
    resolved_leaf_ids_by_node: dict


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
    def __init__(self, experiment):
        self.experiment = experiment

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
        return {
            row.question_type: row.amount
            for row in self.experiment.question_quotas.filter(amount__gt=0)
        }

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
        rules = list(self.experiment.sampling_kcs.select_related("concept").order_by("concept_id"))
        if mode == Experiment.SamplingMode.TOTAL_COUNT:
            if not self.experiment.sampling_total_count or not rules:
                raise SamplingValidationError([
                    self._issue(
                        "TOTAL_COUNT_CONFIGURATION_REQUIRED",
                        "按总题数抽题必须设置总题数并至少选择一个知识点范围。",
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
            candidates = self.total_count_candidates(
                question_bank_id=self.experiment.question_bank_id,
                concept_ids=[rule.concept_id for rule in rules],
                match_scope=scope,
            )
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
                and len(candidates) < self.experiment.sampling_total_count
            ):
                raise SamplingValidationError([
                    self._issue(
                        "TOTAL_COUNT_INSUFFICIENT",
                        f"所选知识点范围需要 {self.experiment.sampling_total_count} 题，可用 {len(candidates)} 题。",
                        field="sampling_total_count",
                    )
                ])
            return SamplingResolution(candidates, {}, type_buckets, resolved)

        questions = list(
            Question.objects.filter(
                bank_id=self.experiment.question_bank_id,
                status=Question.Status.ACTIVE,
            )
            .prefetch_related("concept_links")
            .order_by("sequence", "pk")
        )
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
        return SamplingResolution(candidates, buckets, {}, resolved)

    def select_with_attribution(self, sampling_seed):
        resolution = self.resolve()
        rng = random.Random(int(sampling_seed[:16], 16))
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
                }
                for question in sorted(selected, key=lambda item: (item.sequence, item.pk))
            ]
        rules = list(self.experiment.sampling_kcs.select_related("concept").order_by("concept_id"))
        matching = _maximum_matching(rules, resolution.buckets, rng=rng)
        questions = {question.pk: question for question in resolution.candidates}
        scope_by_concept = {rule.concept_id: rule.match_scope for rule in rules}
        selected = [
            {
                "question": questions[question_id],
                "quota_kc_id": concept_id,
                "quota_question_type": None,
                "match_scope": scope_by_concept[concept_id],
            }
            for (concept_id, _slot), question_id in matching.items()
        ]
        return sorted(selected, key=lambda item: (item["question"].sequence, item["question"].pk))

    def select(self, sampling_seed):
        return [item["question"] for item in self.select_with_attribution(sampling_seed)]
