import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.experiments.models import Experiment
from apps.experiments.test_experiment_purge import purge_test_experiment


class Command(BaseCommand):
    help = '预演或清理 KT01 / EDA技术 / test 的指定实验；默认不修改数据。'

    def add_arguments(self, parser):
        parser.add_argument('--experiment-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int, required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            actor = User.objects.get(pk=options['actor_id'])
            result = purge_test_experiment(experiment_id=options['experiment_id'], actor=actor, apply=options['apply'])
        except (User.DoesNotExist, Experiment.DoesNotExist, ValidationError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
