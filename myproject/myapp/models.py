from django.db import models

# Create your models here.
from django.db import models
import secrets


class CompetitionSettings(models.Model):
    active_round = models.IntegerField(default=1)
    competition_started = models.BooleanField(default=False)
    competition_ended = models.BooleanField(default=False)
    round1_timer = models.IntegerField(default=0, help_text="Timer in seconds, 0 = no timer")
    round2_timer = models.IntegerField(default=0)
    round3_timer = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Competition Settings"

    def __str__(self):
        return f"Settings (Active Round: {self.active_round})"

    def get_timer_for_round(self, round_num):
        return getattr(self, f'round{round_num}_timer', 0)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    token = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def generate_token(self):
        self.token = secrets.token_urlsafe(16)
        return self.token

    def get_total_score(self):
        return sum(s.points for s in self.scores.all())

    def get_round_score(self, round_num):
        return sum(s.points for s in self.scores.filter(round=round_num))


class Question(models.Model):
    round = models.IntegerField()
    question_number = models.IntegerField()
    question_text = models.TextField()
    option_a = models.CharField(max_length=500)
    option_b = models.CharField(max_length=500)
    option_c = models.CharField(max_length=500)
    option_d = models.CharField(max_length=500)
    correct_answer = models.CharField(max_length=1, choices=[
        ('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')
    ])
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('round', 'question_number')
        ordering = ['round', 'question_number']

    def __str__(self):
        return f"Round {self.round} - Q{self.question_number}"

    def get_option(self, letter):
        return getattr(self, f'option_{letter.lower()}', '')


class QuestionRequest(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_ANSWERED = 'answered'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_ANSWERED, 'Answered'),
    ]

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='requests')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='requests')
    round = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.team.name} → Q{self.question.question_number} (Round {self.round}) [{self.status}]"


class Answer(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    question_request = models.OneToOneField(QuestionRequest, on_delete=models.CASCADE, null=True, blank=True)
    selected_answer = models.CharField(max_length=1, choices=[
        ('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')
    ])
    round = models.IntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ('team', 'question')

    def __str__(self):
        return f"{self.team.name} - Q{self.question.question_number}: {self.selected_answer}"

    def save(self, *args, **kwargs):
        self.is_correct = (self.selected_answer.upper() == self.question.correct_answer.upper())
        super().save(*args, **kwargs)


class Score(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='scores')
    answer = models.OneToOneField(Answer, on_delete=models.CASCADE, related_name='score', null=True, blank=True)
    round = models.IntegerField()
    points = models.IntegerField(default=0)
    awarded_at = models.DateTimeField(auto_now=True)
    note = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.team.name} - Round {self.round}: {self.points} pts"