from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum
import json
import secrets

from .models import (
    Team, Question, QuestionRequest, Answer, Score, CompetitionSettings
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def get_team_from_session(request):
    team_id = request.session.get('team_id')
    if team_id:
        try:
            return Team.objects.get(pk=team_id, is_active=True)
        except Team.DoesNotExist:
            pass
    return None


def team_required(view_func):
    def wrapper(request, *args, **kwargs):
        team = get_team_from_session(request)
        if not team:
            return redirect('login')
        return view_func(request, *args, team=team, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_admin'):
            return redirect('admin_login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ─── Auth Views ────────────────────────────────────────────────────────────────

def team_login(request):
    if request.method == 'POST':
        team_name = request.POST.get('team_name', '').strip()
        token = request.POST.get('token', '').strip()
        try:
            team = Team.objects.get(name=team_name, token=token, is_active=True)
            request.session['team_id'] = team.pk
            request.session['team_name'] = team.name
            return redirect('team_dashboard')
        except Team.DoesNotExist:
            messages.error(request, 'Invalid team name or token. Please try again.')
    return render(request, 'login.html')


def team_logout(request):
    request.session.flush()
    return redirect('login')


def admin_login(request):
    if request.method == 'POST':
        password = request.POST.get('password', '')
        if password == 'kcc_admin_2025':
            request.session['is_admin'] = True
            return redirect('admin_dashboard')
        messages.error(request, 'Invalid admin password.')
    return render(request, 'admin_login.html')


def admin_logout(request):
    request.session.pop('is_admin', None)
    return redirect('admin_login')


# ─── Team Views ─────────────────────────────────────────────────────────────────

@team_required
def team_dashboard(request, team=None):
    settings = CompetitionSettings.get_settings()
    active_round = settings.active_round

    # Get all questions for the active round
    questions = Question.objects.filter(round=active_round).order_by('question_number')

    # Build status map for each question number
    question_status = {}
    for q in questions:
        # Check if lockedused by any team
        if q.is_locked:
            question_status[q.question_number] = 'taken'
        else:
            # Check if THIS team requested it
            my_request = QuestionRequest.objects.filter(
                team=team, question=q
            ).first()
            if my_request:
                if my_request.status == QuestionRequest.STATUS_APPROVED:
                    question_status[q.question_number] = 'approved'
                elif my_request.status == QuestionRequest.STATUS_ANSWERED:
                    question_status[q.question_number] = 'answered'
                else:
                    question_status[q.question_number] = 'requested'
            else:
                # Check if another team has an approvedanswered request
                other_request = QuestionRequest.objects.filter(
                    question=q,
                    status__in=[QuestionRequest.STATUS_APPROVED, QuestionRequest.STATUS_ANSWERED]
                ).first()
                if other_request:
                    question_status[q.question_number] = 'taken'
                else:
                    question_status[q.question_number] = 'available'

    # Check for an approved question waiting for this team
    pending_approved = QuestionRequest.objects.filter(
        team=team,
        status=QuestionRequest.STATUS_APPROVED,
        round=active_round
    ).first()

    timer = settings.get_timer_for_round(active_round)

    # Build a flat dict with question_number (int) -> status string
    # Pass it as JSON for the template JS
    import json as _json
    question_status_json = _json.dumps(question_status)

    # Build approved request map: question_number -> request_id
    approved_map = {}
    for req in QuestionRequest.objects.filter(team=team, status=QuestionRequest.STATUS_APPROVED, round=active_round):
        approved_map[req.question.question_number] = req.pk
    approved_map_json = _json.dumps(approved_map)

    return render(request, 'team_dashboard.html', {
        'team': team,
        'settings': settings,
        'questions': questions,
        'question_status': question_status,
        'question_status_json': question_status_json,
        'approved_map_json': approved_map_json,
        'pending_approved': pending_approved,
        'timer': timer,
        'active_round': active_round,
    })


@team_required
@require_POST
def request_question(request, team=None):
    settings = CompetitionSettings.get_settings()
    question_number = int(request.POST.get('question_number'))
    active_round = settings.active_round

    try:
        question = Question.objects.get(round=active_round, question_number=question_number)
    except Question.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Question not found'})

    if question.is_locked:
        return JsonResponse({'success': False, 'error': 'Question already taken'})

    # Check if another team already has approvedanswered this
    existing = QuestionRequest.objects.filter(
        question=question,
        status__in=[QuestionRequest.STATUS_APPROVED, QuestionRequest.STATUS_ANSWERED]
    ).exists()
    if existing:
        return JsonResponse({'success': False, 'error': 'Question already taken by another team'})

    # Check if this team already requested it
    already = QuestionRequest.objects.filter(team=team, question=question).exists()
    if already:
        return JsonResponse({'success': False, 'error': 'Already requested'})

    QuestionRequest.objects.create(
        team=team,
        question=question,
        round=active_round,
        status=QuestionRequest.STATUS_PENDING
    )

    return JsonResponse({'success': True, 'message': f'Question {question_number} requested!'})


@team_required
def answer_question(request, request_id, team=None):
    q_request = get_object_or_404(
        QuestionRequest,
        pk=request_id,
        team=team,
        status=QuestionRequest.STATUS_APPROVED
    )
    question = q_request.question
    settings = CompetitionSettings.get_settings()
    timer = settings.get_timer_for_round(q_request.round)

    if request.method == 'POST':
        selected = request.POST.get('answer', '').upper()
        if selected not in ('A', 'B', 'C', 'D'):
            messages.error(request, 'Please select a valid answer.')
            return redirect('answer_question', request_id=request_id)

        # Check if already answered
        if Answer.objects.filter(team=team, question=question).exists():
            messages.warning(request, 'You already answered this question.')
            return redirect('team_dashboard')

        answer = Answer.objects.create(
            team=team,
            question=question,
            question_request=q_request,
            selected_answer=selected,
            round=q_request.round
        )

        # Create empty score record for admin to fill
        Score.objects.create(
            team=team,
            answer=answer,
            round=q_request.round,
            points=0
        )

        q_request.status = QuestionRequest.STATUS_ANSWERED
        q_request.save()

        question.is_locked = True
        question.save()

        messages.success(request, f'Answer submitted! Awaiting score from admin.')
        return redirect('team_dashboard')

    options = [
        ('A', question.option_a),
        ('B', question.option_b),
        ('C', question.option_c),
        ('D', question.option_d),
    ]

    return render(request, 'answer_question.html', {
        'team': team,
        'question': question,
        'q_request': q_request,
        'options': options,
        'timer': timer,
    })


@team_required
def check_approval(request, team=None):
    """Polling endpoint for teams to check if their request was approved."""
    settings = CompetitionSettings.get_settings()
    approved = QuestionRequest.objects.filter(
        team=team,
        status=QuestionRequest.STATUS_APPROVED,
        round=settings.active_round
    ).first()

    if approved:
        return JsonResponse({
            'approved': True,
            'request_id': approved.pk,
            'question_number': approved.question.question_number
        })
    return JsonResponse({'approved': False})


# ─── Admin Views ───────────────────────────────────────────────────────────────

@admin_required
def admin_dashboard(request):
    settings = CompetitionSettings.get_settings()
    teams = Team.objects.filter(is_active=True)
    pending_requests = QuestionRequest.objects.filter(
        status=QuestionRequest.STATUS_PENDING
    ).select_related('team', 'question').order_by('-requested_at')

    recent_answers = Answer.objects.select_related('team', 'question').order_by('-submitted_at')[:20]

    return render(request, 'admin_dashboard.html', {
        'settings': settings,
        'teams': teams,
        'pending_requests': pending_requests,
        'recent_answers': recent_answers,
        'total_teams': teams.count(),
        'pending_count': pending_requests.count(),
    })


@admin_required
def admin_teams(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate_teams':
            count = int(request.POST.get('count', 1))
            prefix = request.POST.get('prefix', 'Team')
            created = []
            for i in range(1, count + 1):
                name = f"{prefix} {i}"
                if not Team.objects.filter(name=name).exists():
                    team = Team(name=name)
                    team.generate_token()
                    team.save()
                    created.append(team)
            messages.success(request, f'Created {len(created)} teams.')

        elif action == 'add_team':
            name = request.POST.get('name', '').strip()
            if name:
                if Team.objects.filter(name=name).exists():
                    messages.error(request, f'Team "{name}" already exists.')
                else:
                    team = Team(name=name)
                    team.generate_token()
                    team.save()
                    messages.success(request, f'Team "{name}" created with token: {team.token}')

        elif action == 'regen_token':
            team_id = request.POST.get('team_id')
            team = get_object_or_404(Team, pk=team_id)
            team.generate_token()
            team.save()
            messages.success(request, f'Token regenerated for {team.name}.')

        elif action == 'delete_team':
            team_id = request.POST.get('team_id')
            team = get_object_or_404(Team, pk=team_id)
            team.delete()
            messages.success(request, 'Team deleted.')

        elif action == 'clear_all':
            Team.objects.all().delete()
            messages.success(request, 'All teams cleared.')

        return redirect('admin_teams')

    teams = Team.objects.all().order_by('name')
    return render(request, 'admin_teams.html', {'teams': teams})


@admin_required
def admin_questions(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'upload':
            excel_file = request.FILES.get('excel_file')
            if not excel_file:
                messages.error(request, 'No file uploaded.')
                return redirect('admin_questions')

            try:
                import pandas as pd

                xl = pd.ExcelFile(excel_file)
                sheet_names = xl.sheet_names
                count = 0
                errors = []

                # Try multi-sheet format first (each sheet = one round)
                # Sheet names like "Round 1", "Round 2", "Round 3" or just "1","2","3"
                def parse_round_number(name):
                    import re
                    m = re.search(r'\d+', str(name))
                    return int(m.group()) if m else None

                multi_sheet = any(parse_round_number(s) for s in sheet_names)

                if multi_sheet and len(sheet_names) > 1:
                    # Multi-sheet: each sheet is a round
                    for sheet in sheet_names:
                        round_num = parse_round_number(sheet)
                        if round_num is None:
                            continue
                        df = xl.parse(sheet)
                        df.columns = [str(c).strip() for c in df.columns]

                        # Flexible column name mapping
                        col_map = {}
                        for col in df.columns:
                            cl = col.lower().replace(' ', '_').replace('-', '_')
                            if cl in ('question', 'q', 'problem'):
                                col_map['question'] = col
                            elif cl in ('option_a', 'a', 'opt_a', 'choice_a', 'option a'):
                                col_map['a'] = col
                            elif cl in ('option_b', 'b', 'opt_b', 'choice_b', 'option b'):
                                col_map['b'] = col
                            elif cl in ('option_c', 'c', 'opt_c', 'choice_c', 'option c'):
                                col_map['c'] = col
                            elif cl in ('option_d', 'd', 'opt_d', 'choice_d', 'option d'):
                                col_map['d'] = col
                            elif cl in ('answer', 'correct_answer', 'ans', 'correct', 'key'):
                                col_map['answer'] = col
                            elif cl in ('#', 'no', 'number', 'q_no', 'question_number', 'q#'):
                                col_map['number'] = col

                        required = ['question', 'a', 'b', 'c', 'd', 'answer']
                        missing = [r for r in required if r not in col_map]
                        if missing:
                            errors.append(f'Sheet "{sheet}": missing columns for {missing}')
                            continue

                        for idx, row in df.iterrows():
                            try:
                                q_num = int(row[col_map['number']]) if 'number' in col_map else (idx + 1)
                                Question.objects.update_or_create(
                                    round=round_num,
                                    question_number=q_num,
                                    defaults={
                                        'question_text': str(row[col_map['question']]).strip(),
                                        'option_a': str(row[col_map['a']]).strip(),
                                        'option_b': str(row[col_map['b']]).strip(),
                                        'option_c': str(row[col_map['c']]).strip(),
                                        'option_d': str(row[col_map['d']]).strip(),
                                        'correct_answer': str(row[col_map['answer']]).strip().upper()[0],
                                        'is_locked': False,
                                    }
                                )
                                count += 1
                            except Exception as row_err:
                                errors.append(f'Sheet "{sheet}" row {idx+2}: {row_err}')

                else:
                    # Single-sheet format with Round column
                    df = xl.parse(sheet_names[0])
                    df.columns = [str(c).strip() for c in df.columns]

                    col_map = {}
                    for col in df.columns:
                        cl = col.lower().replace(' ', '_').replace('-', '_')
                        if cl in ('round', 'round_no', 'round_number'):
                            col_map['round'] = col
                        elif cl in ('question', 'q', 'problem'):
                            col_map['question'] = col
                        elif cl in ('option_a', 'a', 'opt_a', 'choice_a'):
                            col_map['a'] = col
                        elif cl in ('option_b', 'b', 'opt_b', 'choice_b'):
                            col_map['b'] = col
                        elif cl in ('option_c', 'c', 'opt_c', 'choice_c'):
                            col_map['c'] = col
                        elif cl in ('option_d', 'd', 'opt_d', 'choice_d'):
                            col_map['d'] = col
                        elif cl in ('answer', 'correct_answer', 'ans', 'correct', 'key'):
                            col_map['answer'] = col
                        elif cl in ('#', 'no', 'number', 'q_no', 'question_number'):
                            col_map['number'] = col

                    for idx, row in df.iterrows():
                        try:
                            round_num = int(row[col_map['round']]) if 'round' in col_map else 1
                            q_num = int(row[col_map['number']]) if 'number' in col_map else (idx + 1)
                            Question.objects.update_or_create(
                                round=round_num,
                                question_number=q_num,
                                defaults={
                                    'question_text': str(row[col_map['question']]).strip(),
                                    'option_a': str(row[col_map['a']]).strip(),
                                    'option_b': str(row[col_map['b']]).strip(),
                                    'option_c': str(row[col_map['c']]).strip(),
                                    'option_d': str(row[col_map['d']]).strip(),
                                    'correct_answer': str(row[col_map['answer']]).strip().upper()[0],
                                    'is_locked': False,
                                }
                            )
                            count += 1
                        except Exception as row_err:
                            errors.append(f'Row {idx+2}: {row_err}')

                if count:
                    messages.success(request, f'✓ Imported {count} questions successfully.')
                if errors:
                    for e in errors[:5]:
                        messages.warning(request, e)

            except Exception as e:
                messages.error(request, f'Error reading file: {str(e)}')

        elif action == 'unlock':
            q_id = request.POST.get('question_id')
            q = get_object_or_404(Question, pk=q_id)
            q.is_locked = False
            q.save()
            messages.success(request, f'Question {q.question_number} unlocked.')

        elif action == 'lock':
            q_id = request.POST.get('question_id')
            q = get_object_or_404(Question, pk=q_id)
            q.is_locked = True
            q.save()

        elif action == 'delete_all':
            Question.objects.all().delete()
            messages.success(request, 'All questions deleted.')

        elif action == 'unlock_all':
            Question.objects.all().update(is_locked=False)
            messages.success(request, 'All questions unlocked.')

        return redirect('admin_questions')

    questions_by_round = {}
    for round_num in [1, 2, 3]:
        questions_by_round[round_num] = Question.objects.filter(round=round_num)

    return render(request, 'admin_questions.html', {
        'questions_by_round': questions_by_round,
    })


@admin_required
def admin_requests(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        req_id = request.POST.get('request_id')
        q_request = get_object_or_404(QuestionRequest, pk=req_id)

        if action == 'approve':
            q_request.status = QuestionRequest.STATUS_APPROVED
            q_request.approved_at = timezone.now()
            q_request.save()
            messages.success(request, f'Approved: {q_request.team.name} → Q{q_request.question.question_number}')

        elif action == 'reject':
            q_request.status = QuestionRequest.STATUS_REJECTED
            q_request.save()
            messages.warning(request, f'Rejected request from {q_request.team.name}')

        return redirect('admin_requests')

    pending = QuestionRequest.objects.filter(
        status=QuestionRequest.STATUS_PENDING
    ).select_related('team', 'question').order_by('-requested_at')

    approved = QuestionRequest.objects.filter(
        status=QuestionRequest.STATUS_APPROVED
    ).select_related('team', 'question').order_by('-approved_at')

    all_requests = QuestionRequest.objects.select_related(
        'team', 'question'
    ).order_by('-requested_at')[:50]

    return render(request, 'admin_requests.html', {
        'pending': pending,
        'approved': approved,
        'all_requests': all_requests,
    })


@admin_required
def admin_scoring(request):
    if request.method == 'POST':
        score_id = request.POST.get('score_id')
        points = request.POST.get('points', 0)
        note = request.POST.get('note', '')

        score = get_object_or_404(Score, pk=score_id)
        score.points = int(points)
        score.note = note
        score.save()
        messages.success(request, f'Score updated: {score.team.name} → {points} pts')
        return redirect('admin_scoring')

    answers = Answer.objects.select_related(
        'team', 'question', 'score'
    ).order_by('-submitted_at')

    # Ensure every answer has a score object
    for answer in answers:
        if not hasattr(answer, 'score') or answer.score is None:
            Score.objects.get_or_create(
                answer=answer,
                defaults={'team': answer.team, 'round': answer.round, 'points': 0}
            )

    answers = Answer.objects.select_related('team', 'question', 'score').order_by('-submitted_at')

    return render(request, 'admin_scoring.html', {'answers': answers})


@admin_required
def admin_settings(request):
    settings = CompetitionSettings.get_settings()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'set_round':
            round_num = int(request.POST.get('round', 1))
            settings.active_round = round_num
            settings.save()
            messages.success(request, f'Active round set to Round {round_num}.')

        elif action == 'update_timers':
            settings.round1_timer = int(request.POST.get('round1_timer', 0))
            settings.round2_timer = int(request.POST.get('round2_timer', 0))
            settings.round3_timer = int(request.POST.get('round3_timer', 0))
            settings.save()
            messages.success(request, 'Timers updated.')

        elif action == 'start':
            settings.competition_started = True
            settings.competition_ended = False
            settings.save()
            messages.success(request, 'Competition started!')

        elif action == 'end':
            settings.competition_ended = True
            settings.save()
            messages.success(request, 'Competition ended.')

        elif action == 'reset':
            Answer.objects.all().delete()
            Score.objects.all().delete()
            QuestionRequest.objects.all().delete()
            Question.objects.all().update(is_locked=False)
            settings.active_round = 1
            settings.competition_started = False
            settings.competition_ended = False
            settings.save()
            messages.success(request, 'Competition reset successfully.')

        return redirect('admin_settings')

    return render(request, 'admin_settings.html', {'settings': settings})


@admin_required
def admin_leaderboard(request):
    settings = CompetitionSettings.get_settings()
    teams = Team.objects.filter(is_active=True)

    leaderboard = []
    for team in teams:
        r1 = team.get_round_score(1)
        r2 = team.get_round_score(2)
        r3 = team.get_round_score(3)
        total = r1 + r2 + r3
        leaderboard.append({
            'team': team,
            'round1': r1,
            'round2': r2,
            'round3': r3,
            'total': total,
        })

    leaderboard.sort(key=lambda x: x['total'], reverse=True)

    # Add rank
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1

    return render(request, 'admin_leaderboard.html', {
        'leaderboard': leaderboard,
        'settings': settings,
    })


def public_leaderboard(request):
    settings = CompetitionSettings.get_settings()
    teams = Team.objects.filter(is_active=True)

    leaderboard = []
    for team in teams:
        r1 = team.get_round_score(1)
        r2 = team.get_round_score(2)
        r3 = team.get_round_score(3)
        total = r1 + r2 + r3
        leaderboard.append({
            'team': team,
            'round1': r1,
            'round2': r2,
            'round3': r3,
            'total': total,
        })

    leaderboard.sort(key=lambda x: x['total'], reverse=True)
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1

    return render(request, 'leaderboard.html', {
        'leaderboard': leaderboard,
        'settings': settings,
    })


def final_results(request):
    settings = CompetitionSettings.get_settings()
    teams = Team.objects.filter(is_active=True)

    leaderboard = []
    for team in teams:
        r1 = team.get_round_score(1)
        r2 = team.get_round_score(2)
        r3 = team.get_round_score(3)
        total = r1 + r2 + r3
        leaderboard.append({
            'team': team,
            'round1': r1,
            'round2': r2,
            'round3': r3,
            'total': total,
        })

    leaderboard.sort(key=lambda x: x['total'], reverse=True)
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1

    return render(request, 'final_results.html', {
        'leaderboard': leaderboard,
        'settings': settings,
        'winner': leaderboard[0] if leaderboard else None,
    })


# ─── API Endpoints ─────────────────────────────────────────────────────────────

def api_leaderboard(request):
    teams = Team.objects.filter(is_active=True)
    data = []
    for team in teams:
        r1 = team.get_round_score(1)
        r2 = team.get_round_score(2)
        r3 = team.get_round_score(3)
        data.append({
            'name': team.name,
            'round1': r1,
            'round2': r2,
            'round3': r3,
            'total': r1 + r2 + r3,
        })
    data.sort(key=lambda x: x['total'], reverse=True)
    return JsonResponse({'leaderboard': data})


def api_pending_requests(request):
    if not request.session.get('is_admin'):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    pending = QuestionRequest.objects.filter(
        status=QuestionRequest.STATUS_PENDING
    ).select_related('team', 'question')
    data = [
        {
            'id': r.pk,
            'team': r.team.name,
            'question_number': r.question.question_number,
            'round': r.round,
            'requested_at': r.requested_at.strftime('%H:%M:%S'),
        }
        for r in pending
    ]
    return JsonResponse({'requests': data, 'count': len(data)})