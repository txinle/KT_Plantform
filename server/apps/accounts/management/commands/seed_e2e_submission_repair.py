from apps.experiments.models import Experiment
from .seed_e2e_capture_initialization import Command as InitializationSeed


class Command(InitializationSeed):
    help = 'Create isolated pre-submit confidence and recovery browser fixtures in kt_e2e.'
    confidence_mode = Experiment.ConfidenceMode.ALL

    def _student(self, class_group):
        student = super()._student(class_group)
        student.set_password(f'S{student.student_number}')
        student.uses_initial_password = True
        student.save(update_fields=['password', 'uses_initial_password'])
        return student
