"""A quiz where nothing matched must not write a draft.

Recurring schedules run every day; at 0% coverage the old code still filled
random answers and saved them, so each morning deposited another batch of wrong
answers into quizzes the user may later answer by hand.
"""

import app.services.course.chaoxing.quiz_service as qs

_QUESTIONS = {"questions": [{"id": "1", "answerField": {}}], "answerwqbid": "1,", "pyFlag": ""}


class _Resp:
    status_code = 200
    text = "<html></html>"

    def json(self):
        return {"status": True, "msg": "保存成功！"}


class _Session:
    def get(self, *args, **kwargs):
        return _Resp()

    def post(self, *args, **kwargs):
        return _Resp()


class _SessionManager:
    def get_session(self):
        return _Session()


class _Tiku:
    DISABLE = False
    COVER_RATE = 0.9

    def get_submit_params(self):
        return "0"

    def wants_concurrent_query(self):
        return False


class _Processor:
    rate = 0.0

    def __init__(self, *args, **kwargs):
        pass

    def process_questions(self, questions, html=""):
        return type(self).rate


def _service(monkeypatch, rate):
    _Processor.rate = rate
    monkeypatch.setattr(qs, "decode_questions_info", lambda html: dict(_QUESTIONS))
    monkeypatch.setattr(qs, "QuizAnswerProcessor", _Processor)
    return qs.ChaoxingQuizService(
        tiku=_Tiku(), rollback_times=0, kwargs={}, session_manager=_SessionManager()
    )


def _course():
    return {"clazzId": "1", "courseId": "2"}


def _job():
    return {"jobid": "work-1", "enc": "x"}, {"knowledgeid": "1", "ktoken": "k", "cpi": "1"}


def test_zero_coverage_does_not_touch_the_draft(monkeypatch):
    service = _service(monkeypatch, rate=0.0)
    filled = []
    monkeypatch.setattr(qs, "fill_answers_into_form", lambda q, is_save: filled.append(is_save))

    job, info = _job()
    assert service.study_work(_course(), job, info) is True
    assert filled == [], "一题未命中时不应填写/保存任何答案"


def test_partial_coverage_still_submits_or_saves(monkeypatch):
    """Only the 0% case is skipped: partial coverage keeps the existing behaviour."""
    service = _service(monkeypatch, rate=50.0)
    filled = []
    monkeypatch.setattr(qs, "fill_answers_into_form", lambda q, is_save: filled.append(is_save))

    job, info = _job()
    service.study_work(_course(), job, info)

    assert filled, "有命中时仍应写入表单"


def test_the_quiz_reports_its_yield_to_the_caller(monkeypatch):
    """The manager needs the yield to tell an idle run from a productive one.

    Uses the REAL QuizAnswerProcessor.process_questions so the reporting line is
    actually exercised (stubbing it out would assert nothing).
    """
    seen = []
    real = qs.QuizAnswerProcessor

    def _processor(tiku, kwargs):
        # Real class, but a tiku whose query always misses: zero yield.
        class _MissTiku(_Tiku):
            def query(self, q):
                return None

        return real(_MissTiku(), kwargs)

    monkeypatch.setattr(qs, "decode_questions_info", lambda html: dict(_QUESTIONS))
    monkeypatch.setattr(qs, "QuizAnswerProcessor", _processor)
    service = qs.ChaoxingQuizService(
        tiku=_Tiku(),
        rollback_times=0,
        kwargs={"quiz_result_callback": lambda found, total: seen.append((found, total))},
        session_manager=_SessionManager(),
    )

    job, info = _job()
    service.study_work(_course(), job, info)

    assert seen, "必须把本轮命中数上报给调用方"
    assert seen[0] == (0, 1), "0 道命中 / 共 1 道题"
