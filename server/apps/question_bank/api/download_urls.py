from django.urls import path
from apps.api.downloads import CSV, DOCX, XLSX, download_view
from .. import downloads


urlpatterns = [
    path('import-templates/question-images.zip', download_view(downloads.question_image_examples, media_types=('application/zip',),
         operation_id='teacher_question_image_examples_zip'), name='teacher-question-image-examples'),
    path('import-templates/question-groups.csv', download_view(downloads.question_group_import_template,
         operation_id='teacher_question_group_import_template_csv'), {'extension': 'csv'}, name='teacher-question-group-import-template-csv'),
    path('import-templates/question-groups.xlsx', download_view(downloads.question_group_import_template, media_types=(XLSX,),
         operation_id='teacher_question_group_import_template_xlsx'), {'extension': 'xlsx'}, name='teacher-question-group-import-template-excel'),
    path('import-templates/question-groups.docx', download_view(downloads.question_group_import_template, media_types=(DOCX,),
         operation_id='teacher_question_group_import_template_docx'), {'extension': 'docx'}, name='teacher-question-group-import-template-word'),
    path('import-templates/questions.csv', download_view(downloads.question_import_template,
         operation_id='teacher_question_import_template_csv'), name='teacher-question-import-template'),
    path('import-templates/questions.xlsx', download_view(downloads.question_import_template_excel, media_types=(XLSX,),
         operation_id='teacher_question_import_template_xlsx'), name='teacher-question-import-template-excel'),
    path('import-templates/questions.docx', download_view(downloads.question_import_template_word, media_types=(DOCX,),
         operation_id='teacher_question_import_template_docx'), name='teacher-question-import-template-word'),
    path('import-templates/kcs.csv', download_view(downloads.kc_import_template,
         operation_id='teacher_kc_import_template'), name='teacher-kc-import-template'),
    path('import-templates/course-kcs.csv', download_view(downloads.course_kc_import_template,
         operation_id='teacher_course_kc_import_template'), name='teacher-course-kc-import-template'),
]
