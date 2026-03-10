from django.contrib import admin

from .models import (
    CompetitionSettings,
    Team,
    Question,
    QuestionRequest,
    Answer,
    Score
)

admin.site.register(CompetitionSettings)
admin.site.register(Team)
admin.site.register(Question)
admin.site.register(QuestionRequest)
admin.site.register(Answer)
admin.site.register(Score)
