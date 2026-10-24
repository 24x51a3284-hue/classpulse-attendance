import os, csv, io, time, shutil
from datetime import date, timedelta
from functools import wraps
from flask import Flask, request, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, case
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(BASE, "data"); os.makedirs(DATA, exist_ok=True)
DBFILE = os.path.join(DATA, "attendance.db"); OLDDB = os.path.join(BASE, "instance", "attendance.db")
if not os.path.exists(DBFILE) and os.path.exists(OLDDB): shutil.copy(OLDDB, DBFILE)  # keep data from older versions

def secret():
    if os.environ.get("SECRET_KEY"): return os.environ["SECRET_KEY"]
    p = os.path.join(DATA, "secret.key")
    if not os.path.exists(p): open(p, "w").write(os.urandom(32).hex())
    return open(p).read().strip()

FAILS = {}
app = Flask(__name__, static_folder="static", static_url_path="")
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///" + DBFILE),
    SECRET_KEY=secret(),
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")
db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    role = db.Column(db.String(10), nullable=False)  # admin / teacher / student
    pw = db.Column(db.String(256), nullable=False)
    must_change = db.Column(db.Boolean, default=True, nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    roll = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    periods = db.Column(db.Integer, nullable=False, default=1)
    __table_args__ = (db.UniqueConstraint("day", "subject_id"),)

class Mark(db.Model):
    session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), primary_key=True)
    present = db.Column(db.Boolean, nullable=False)

class Holiday(db.Model):
    day = db.Column(db.Date, primary_key=True)

class Setting(db.Model):
    key = db.Column(db.String(32), primary_key=True)
    value = db.Column(db.String(64), nullable=False)

class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, server_default=func.now())
    who = db.Column(db.String(64))
    text = db.Column(db.String(300))


class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dow = db.Column(db.Integer, nullable=False)      # 0=Mon ... 5=Sat
    start = db.Column(db.Integer, nullable=False)    # first period 1..7
    span = db.Column(db.Integer, nullable=False, default=1)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"))
    label = db.Column(db.String(40))

class SubjectInfo(db.Model):
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), primary_key=True)
    full = db.Column(db.String(120))
    teacher = db.Column(db.String(200))

FSD = "Mr.R.Ranganath Raju / Mr.K.Srinivasulu"
DEF_SUBJ = {
    "OS": ("Operating Systems", "Mrs.S.Reshma"),
    "SE": ("Software Engineering", "Mr.S.Seetharamudu"),
    "ML": ("Machine Learning", "Dr.N.Venkatesh Naik"),
    "ECE": ("English for Competitive Examinations", "Mr.M.Raja"),
    "IQTA": ("Introduction Quantum Technologies & Application", "Mrs.K.Salma Khatoon"),
    "FSD-T": ("Full Stack Development-I (Theory)", FSD),
    "FSD Lab": ("Full Stack Development-I Lab", FSD),
    "OS Lab": ("Operating Systems Lab", "Mrs.S.Reshma / Mrs.S.Sadiya Fathima"),
    "ML Lab": ("Machine Learning Lab", "Dr.N.Venkatesh Naik / Mrs.K.Salma Khatoon"),
    "TN Lab": ("Tinkering Lab", "Mrs.V.Nagamani"),
    "MM": ("MM (name to confirm)", ""),
}
T = None  # None = Training (not tracked)
DEF_SLOTS = [
    (0,1,1,"OS"),(0,2,1,"SE"),(0,3,2,T),(0,5,1,"IQTA"),(0,6,2,"FSD Lab"),
    (1,1,2,"OS Lab"),(1,3,2,T),(1,5,1,"IQTA"),(1,6,1,"SE"),(1,7,1,"ECE"),
    (2,1,1,"OS"),(2,2,1,"ML"),(2,3,2,T),(2,5,1,"IQTA"),(2,6,1,"ML"),(2,7,1,"SE"),
    (3,1,1,"ML"),(3,2,1,"ECE"),(3,3,2,T),(3,5,1,"OS"),(3,6,2,"TN Lab"),
    (4,1,1,"OS"),(4,2,1,"FSD-T"),(4,3,2,T),(4,5,1,"ML"),(4,6,2,"ML Lab"),
    (5,1,1,"IQTA"),(5,2,1,"ECE"),(5,3,2,T),(5,5,1,"MM"),(5,6,1,"SE"),(5,7,1,"FSD-T"),
]

def load_default():
    for n, (full, t) in DEF_SUBJ.items():
        s = Subject.query.filter_by(name=n).first()
        if not s:
            s = Subject(name=n); db.session.add(s); db.session.flush()
        i = db.session.get(SubjectInfo, s.id) or SubjectInfo(subject_id=s.id)
        i.full, i.teacher = full, t; db.session.add(i)
    Slot.query.delete()
    for dow, start, span, n in DEF_SLOTS:
        s = Subject.query.filter_by(name=n).first() if n else None
        db.session.add(Slot(dow=dow, start=start, span=span, subject_id=s.id if s else None, label=n or "Training"))

def tt_slots():
    names = {x.id: x.name for x in Subject.query}
    return [{"d": x.dow, "s": x.start, "n": x.span, "sub": names.get(x.subject_id), "label": x.label}
            for x in Slot.query.order_by(Slot.dow, Slot.start)]

def tt_info():
    names = {x.id: x.name for x in Subject.query}
    return {names[i.subject_id]: {"full": i.full, "teacher": i.teacher} for i in SubjectInfo.query if i.subject_id in names}

class Assessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    day = db.Column(db.Date, nullable=False)
    max_marks = db.Column(db.Float, nullable=False, default=25)

class Score(db.Model):
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessment.id"), primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), primary_key=True)
    marks = db.Column(db.Float)

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    author = db.Column(db.String(64))
    action = db.Column(db.String(40))
    text = db.Column(db.String(400))
    status = db.Column(db.String(10), default="open")
    ts = db.Column(db.DateTime, server_default=func.now())

def err(msg, code=400):
    return jsonify(error=msg), code

def log(t):
    db.session.add(Audit(who=session.get("u", "system"), text=t[:300]))

def me():
    u = session.get("u")
    return User.query.filter_by(username=u).first() if u else None

def need(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u:
                return err("login", 401)
            if u.must_change:
                return err("change_password", 403)
            if roles and u.role not in roles:
                return err("Not allowed", 403)
            return f(*a, **k)
        return w
    return deco

def th():
    s = db.session.get(Setting, "th")
    return int(s.value) if s else 75

def mk_user(username, role, password):
    db.session.add(User(username=username.lower(), role=role, pw=generate_password_hash(password), must_change=True))


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    name = (d.get("username") or "").strip().lower(); key = (request.remote_addr, name); now = time.time()
    f = [t for t in FAILS.get(key, []) if now - t < 600]
    if len(f) >= 5: return err("Too many wrong attempts. Try again in 10 minutes", 429)
    u = User.query.filter_by(username=name).first()
    if not u or not check_password_hash(u.pw, d.get("password") or ""):
        FAILS[key] = f + [now]
        return err("Wrong username or password", 401)
    FAILS.pop(key, None)
    session.clear(); session["u"] = u.username
    return jsonify(user=u.username, role=u.role, must_change=u.must_change)

@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@app.get("/api/me")
def whoami():
    u = me()
    return jsonify(user=u.username, role=u.role, must_change=u.must_change) if u else err("login", 401)

@app.post("/api/password")
def change_pw():
    u = me()
    if not u: return err("login", 401)
    d = request.get_json(silent=True) or {}
    new = d.get("new") or ""
    if not check_password_hash(u.pw, d.get("old") or ""): return err("Current password is wrong")
    if len(new) < 6 or new.lower() == u.username or new == "admin123": return err("Use a different password with 6+ characters")
    u.pw = generate_password_hash(new); u.must_change = False
    log("Password changed"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/bootstrap")
@need()
def bootstrap():
    u = me()
    q = Student.query.order_by(Student.roll)
    studs = [s for s in q.all() if u.role != "student" or s.roll.lower() == u.username]
    return jsonify(role=u.role, user=u.username, th=th(),
                   students=[{"r": s.roll, "n": s.name} for s in studs],
                   subjects=[s.name for s in Subject.query.order_by(Subject.id)],
                   holidays=sorted(h.day.isoformat() for h in Holiday.query),
                   timetable=tt_slots(), info=tt_info())

def find_session(day, subject, create=False, periods=1):
    sub = Subject.query.filter_by(name=subject).first()
    if not sub: return None, None
    cs = ClassSession.query.filter_by(day=day, subject_id=sub.id).first()
    if not cs and create:
        cs = ClassSession(day=day, subject_id=sub.id, periods=periods); db.session.add(cs); db.session.flush()
    return sub, cs

@app.get("/api/marks")
@need("teacher", "admin")
def get_marks():
    try: day = date.fromisoformat(request.args.get("date", ""))
    except ValueError: return err("Bad date")
    sub, cs = find_session(day, request.args.get("subject", ""))
    if not cs: return jsonify(periods=None, marks={})
    rows = db.session.query(Student.roll, Mark.present).join(Mark, Mark.student_id == Student.id).filter(Mark.session_id == cs.id).all()
    return jsonify(periods=cs.periods, marks={r: int(p) for r, p in rows})

@app.post("/api/marks")
@need("teacher", "admin")
def post_marks():
    d = request.get_json(silent=True) or {}
    try: day = date.fromisoformat(d.get("date", "")); p = max(1, min(8, int(d.get("periods", 1))))
    except (ValueError, TypeError): return err("Bad date or periods")
    sub, cs = find_session(day, d.get("subject", ""), True, p)
    if not sub: return err("Unknown subject")
    cs.periods = p
    for s in Student.query.filter(Student.roll.in_(list((d.get("marks") or {}).keys()))):
        m = db.session.get(Mark, (cs.id, s.id)) or Mark(session_id=cs.id, student_id=s.id, present=True)
        m.present = bool(d["marks"][s.roll]); db.session.add(m)
    log(f"Marked {sub.name} on {day} ({p} periods)"); db.session.commit()
    return jsonify(ok=True)

@app.post("/api/special")
@need("teacher", "admin")
def special():
    d = request.get_json(silent=True) or {}
    try:
        a = date.fromisoformat(d["from"]); b = date.fromisoformat(d.get("to") or d["from"]); p = max(1, min(8, int(d.get("periods", 1))))
    except (KeyError, ValueError, TypeError): return err("Bad dates")
    if b < a or (b - a).days > 366: return err("Date range must be within one year")
    studs = Student.query.filter(Student.roll.in_(d.get("rolls") or [])).all()
    if not studs: return err("Select at least one student")
    hol = {h.day for h in Holiday.query}
    added = skipped = days = 0
    day = a
    from datetime import timedelta
    while day <= b:
        if day.weekday() != 6 and day not in hol:
            sub, cs = find_session(day, d.get("subject", ""), True, p)
            if not sub: return err("Unknown subject")
            days += 1
            for s in studs:
                m = db.session.get(Mark, (cs.id, s.id))
                if m and m.present: continue
                if m and not d.get("overwrite"): skipped += 1; continue
                if m: m.present = True
                else: db.session.add(Mark(session_id=cs.id, student_id=s.id, present=True))
                added += 1
        day += timedelta(days=1)
    log(f"{d.get('type','Special')}: {len(studs)} student(s), {d.get('subject')}, {a} to {b}, {d.get('note','')} ({added} added)")
    db.session.commit()
    return jsonify(added=added, days=days, skipped=skipped)

def totals(student_id=None):
    q = db.session.query(Mark.student_id, ClassSession.subject_id, func.sum(ClassSession.periods),
                         func.sum(case((Mark.present, ClassSession.periods), else_=0))
                         ).join(ClassSession, Mark.session_id == ClassSession.id)
    if student_id: q = q.filter(Mark.student_id == student_id)
    return q.group_by(Mark.student_id, ClassSession.subject_id).all()

@app.get("/api/stats/<roll>")
@need()
def stats(roll):
    u = me()
    if u.role == "student" and roll.lower() != u.username: return err("Not allowed", 403)
    s = Student.query.filter_by(roll=roll).first()
    if not s: return err("No such student", 404)
    names = {x.id: x.name for x in Subject.query}
    subj = {n: {"a": 0, "t": 0} for n in names.values()}; A = T = 0
    for _, sid, t, a in totals(s.id):
        subj[names[sid]] = {"a": int(a), "t": int(t)}; A += int(a); T += int(t)
    mk = db.session.query(Assessment.name, Subject.name, Score.marks, Assessment.max_marks).join(Assessment, Score.assessment_id == Assessment.id).join(Subject, Assessment.subject_id == Subject.id).filter(Score.student_id == s.id).order_by(Assessment.day).all()
    return jsonify(overall={"a": A, "t": T}, subjects=subj, marks=[{"name": n, "subject": sj, "got": g, "max": m} for n, sj, g, m in mk])

def overall_rows():
    agg = {}
    for stid, _, t, a in totals():
        x = agg.setdefault(stid, [0, 0]); x[0] += int(a); x[1] += int(t)
    return agg

@app.get("/api/shortage")
@need("admin")
def shortage():
    agg = overall_rows(); out = []
    for s in Student.query:
        a, t = agg.get(s.id, [0, 0])
        if t and a * 100 / t < th(): out.append({"r": s.roll, "n": s.name, "p": round(a * 100 / t, 1)})
    return jsonify(sorted(out, key=lambda x: x["p"]))

@app.get("/api/export.csv")
@need("admin")
def export():
    names = {x.id: x.name for x in Subject.query}; per = {}
    for stid, sid, t, a in totals(): per[(stid, sid)] = (int(a), int(t))
    agg = overall_rows(); buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Roll", "Name"] + list(names.values()) + ["Overall"])
    for s in Student.query.order_by(Student.roll):
        row = [s.roll, s.name]
        for sid in names:
            a, t = per.get((s.id, sid), (0, 0)); row.append(round(a * 100 / t) if t else "")
        a, t = agg.get(s.id, [0, 0]); row.append(round(a * 100 / t, 1) if t else "")
        w.writerow(row)
    return app.response_class(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=attendance.csv"})

@app.post("/api/setup")
@need("admin")
def setup():
    d = request.get_json(silent=True) or {}
    studs = {s["r"].strip(): s["n"].strip() for s in d.get("students", []) if s.get("r", "").strip()}
    subs = [x.strip() for x in d.get("subjects", []) if x.strip()]
    if not studs or not subs: return err("Add at least one student and subject")
    try: hol = {date.fromisoformat(x) for x in d.get("holidays", []) if x}
    except ValueError: return err("Holidays must look like 2026-10-31")
    rs = [s.roll for s in Student.query.filter(~Student.roll.in_(list(studs)))]; rsub = [s.name for s in Subject.query.filter(~Subject.name.in_(subs))]
    if (rs or rsub) and not d.get("confirm_delete"):
        return jsonify(error="This permanently deletes attendance and marks for removed students or subjects.", need_confirm=True, students=rs, subjects=rsub), 409
    for roll, name in studs.items():
        s = Student.query.filter_by(roll=roll).first()
        if s: s.name = name
        else:
            db.session.add(Student(roll=roll, name=name))
            if not User.query.filter_by(username=roll.lower()).first(): mk_user(roll, "student", roll.lower())
    for s in Student.query.filter(~Student.roll.in_(list(studs))).all():
        Mark.query.filter_by(student_id=s.id).delete(); Score.query.filter_by(student_id=s.id).delete(); Note.query.filter_by(student_id=s.id).delete(); User.query.filter_by(username=s.roll.lower(), role="student").delete(); db.session.delete(s)
    for n in subs:
        if not Subject.query.filter_by(name=n).first(): db.session.add(Subject(name=n))
    for s in Subject.query.filter(~Subject.name.in_(subs)).all():
        Slot.query.filter_by(subject_id=s.id).delete(); SubjectInfo.query.filter_by(subject_id=s.id).delete()
        for a in Assessment.query.filter_by(subject_id=s.id).all(): Score.query.filter_by(assessment_id=a.id).delete(); db.session.delete(a)
        for cs in ClassSession.query.filter_by(subject_id=s.id).all():
            Mark.query.filter_by(session_id=cs.id).delete(); db.session.delete(cs)
        db.session.delete(s)
    Holiday.query.delete()
    for h in hol: db.session.add(Holiday(day=h))
    log("Setup updated"); db.session.commit()
    return jsonify(ok=True)

@app.post("/api/threshold")
@need("admin")
def set_th():
    v = (request.get_json(silent=True) or {}).get("th")
    if not isinstance(v, int) or not 1 <= v <= 99: return err("Enter a whole number from 1 to 99")
    db.session.merge(Setting(key="th", value=str(v))); log(f"Minimum attendance set to {v}"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/users")
@need("admin")
def users():
    return jsonify([u.username for u in User.query.filter_by(role="teacher").order_by(User.username)])

@app.post("/api/users")
@need("admin")
def add_teacher():
    d = request.get_json(silent=True) or {}
    u = (d.get("username") or "").strip().lower(); p = d.get("password") or ""
    if not u or len(p) < 6: return err("Enter a username and a 6+ character temporary password")
    if User.query.filter_by(username=u).first(): return err("That username already exists")
    mk_user(u, "teacher", p); log(f"Teacher added: {u}"); db.session.commit()
    return jsonify(ok=True)

@app.post("/api/users/reset")
@need("admin")
def reset_pw():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(username=(d.get("username") or "").strip().lower()).first()
    if not u: return err("No such user", 404)
    u.pw = generate_password_hash(d.get("password") or u.username); u.must_change = True
    log(f"Password reset: {u.username}"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/audit")
@need("admin")
def audit():
    return jsonify([{"ts": a.ts.strftime("%d %b %H:%M") if a.ts else "", "who": a.who, "t": a.text}
                    for a in Audit.query.order_by(Audit.id.desc()).limit(40)])

RK_DEF = {"att_high": 60, "marks_high": 35, "marks_med": 50, "drop": 10, "consec": 3}

def rk_cfg():
    c = dict(RK_DEF)
    for s in Setting.query.filter(Setting.key.like("rk_%")):
        c[s.key[3:]] = float(s.value)
    return c

def risk_rows():
    cfg, mn = rk_cfg(), th()
    studs = Student.query.order_by(Student.roll).all()
    by, days = {}, set()
    q = db.session.query(Mark.student_id, ClassSession.day, ClassSession.periods, Mark.present).join(ClassSession, Mark.session_id == ClassSession.id).all()
    for sid, day, per, pr in q:
        by.setdefault(sid, []).append((day, per, bool(pr))); days.add(day)
    cut = max(days) - timedelta(days=14) if days else None
    sc = {}
    for sid, day, m, mx in db.session.query(Score.student_id, Assessment.day, Score.marks, Assessment.max_marks).join(Assessment, Score.assessment_id == Assessment.id).filter(Score.marks.isnot(None)):
        sc.setdefault(sid, []).append((day, m * 100 / mx))
    nn = dict(db.session.query(Note.student_id, func.count(Note.id)).filter(Note.status == "open").group_by(Note.student_id).all())
    pc = lambda r: (sum(p for _, p, x in r if x) * 100 / sum(p for _, p, x in r)) if r else None
    out = []
    for s in studs:
        recs = by.get(s.id, []); att = pc(recs)
        ra = pc([r for r in recs if cut and r[0] > cut]); oa = pc([r for r in recs if cut and r[0] <= cut])
        perday = {}
        for d, p, x in recs: perday[d] = perday.get(d, False) or x
        run = 0
        for d in sorted(perday, reverse=True):
            if perday[d]: break
            run += 1
        ms = sorted(sc.get(s.id, [])); mavg = sum(p for _, p in ms) / len(ms) if ms else None
        pa = la = None
        if ms:
            last = max(d for d, _ in ms); L = [p for d, p in ms if d == last]; P = [p for d, p in ms if d != last]
            if P: pa, la = sum(P) / len(P), sum(L) / len(L)
        score, why = 0, []
        if att is not None:
            if att < cfg["att_high"]: score += 3; why.append(f"Attendance {att:.0f}% is below {cfg['att_high']:.0f}%")
            elif att < mn: score += 2; why.append(f"Attendance {att:.0f}% is below the {mn}% minimum")
        if ra is not None and oa is not None and oa - ra >= cfg["drop"]:
            score += 2; why.append(f"Attendance is falling: {ra:.0f}% in the last 2 weeks vs {oa:.0f}% before")
        if run >= cfg["consec"]: score += 2; why.append(f"Absent {run} days in a row")
        if mavg is not None:
            if mavg < cfg["marks_high"]: score += 3; why.append(f"Average test marks {mavg:.0f}% (below {cfg['marks_high']:.0f}%)")
            elif mavg < cfg["marks_med"]: score += 2; why.append(f"Average test marks {mavg:.0f}% (below {cfg['marks_med']:.0f}%)")
        if pa is not None and pa - la >= cfg["drop"]: score += 1; why.append(f"Latest test marks fell from {pa:.0f}% to {la:.0f}%")
        if att is not None and mavg is not None and att < mn and mavg < cfg["marks_med"]: score += 1; why.append("Low attendance and low marks together")
        rd = lambda v: None if v is None else round(v, 1)
        out.append({"roll": s.roll, "name": s.name, "att": rd(att), "recent": rd(ra), "marks": rd(mavg), "absent_run": run,
                    "score": score, "level": "High" if score >= 4 else "Medium" if score >= 2 else "Low",
                    "reasons": why, "open_notes": nn.get(s.id, 0)})
    return out

@app.get("/api/risk")
@need("teacher", "admin")
def risk():
    rows = risk_rows(); o = {"High": 0, "Medium": 1, "Low": 2}
    rows.sort(key=lambda r: (o[r["level"]], -r["score"], r["roll"]))
    early = sum(1 for r in rows if r["level"] != "Low" and r["att"] is not None and r["att"] >= th())
    cnt = lambda l: sum(1 for r in rows if r["level"] == l)
    return jsonify(rows=rows, summary={"high": cnt("High"), "medium": cnt("Medium"), "low": cnt("Low"), "early": early, "th": th()})

@app.get("/api/risk/config")
@need("admin")
def get_rk():
    return jsonify(rk_cfg())

@app.post("/api/risk/config")
@need("admin")
def set_rk():
    d = request.get_json(silent=True) or {}
    for k in RK_DEF:
        if k in d:
            try: v = float(d[k])
            except (TypeError, ValueError): return err("Rules must be numbers")
            if not 0 <= v <= 100: return err("Rules must be between 0 and 100")
            db.session.merge(Setting(key="rk_" + k, value=str(v)))
    log("Risk rules updated"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/notes")
@need("teacher", "admin")
def get_notes():
    s = Student.query.filter_by(roll=request.args.get("roll", "")).first()
    if not s: return err("No such student", 404)
    return jsonify([{"id": n.id, "author": n.author, "action": n.action, "text": n.text, "status": n.status,
                     "ts": n.ts.strftime("%d %b %H:%M") if n.ts else ""} for n in Note.query.filter_by(student_id=s.id).order_by(Note.id.desc())])

@app.post("/api/notes")
@need("teacher", "admin")
def add_note():
    d = request.get_json(silent=True) or {}
    s = Student.query.filter_by(roll=d.get("roll", "")).first()
    if not s or not (d.get("text") or "").strip(): return err("Write a note for a valid student")
    db.session.add(Note(student_id=s.id, author=session["u"], action=(d.get("action") or "Other")[:40], text=d["text"].strip()[:400]))
    log(f"Follow-up note added for {s.roll}"); db.session.commit()
    return jsonify(ok=True)

@app.post("/api/notes/status")
@need("teacher", "admin")
def note_status():
    d = request.get_json(silent=True) or {}
    n = db.session.get(Note, d.get("id"))
    if not n or d.get("status") not in ("open", "resolved"): return err("Bad request")
    n.status = d["status"]; log(f"Note {n.id} marked {n.status}"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/assessments")
@need("teacher", "admin")
def list_as():
    names = {x.id: x.name for x in Subject.query}
    return jsonify([{"id": a.id, "subject": names.get(a.subject_id), "name": a.name, "day": a.day.isoformat(), "max": a.max_marks}
                    for a in Assessment.query.order_by(Assessment.day.desc(), Assessment.id.desc())])

@app.post("/api/assessments")
@need("teacher", "admin")
def add_as():
    d = request.get_json(silent=True) or {}
    sub = Subject.query.filter_by(name=d.get("subject", "")).first()
    try: day = date.fromisoformat(d.get("date", "")); mx = float(d.get("max", 0))
    except (ValueError, TypeError): return err("Bad date or max marks")
    if not sub or mx <= 0 or not (d.get("name") or "").strip(): return err("Enter subject, test name and max marks")
    db.session.add(Assessment(subject_id=sub.id, name=d["name"].strip()[:80], day=day, max_marks=mx))
    log(f"Test created: {sub.name} {d['name']}"); db.session.commit()
    return jsonify(ok=True)

@app.get("/api/scores")
@need("teacher", "admin")
def get_scores():
    rows = db.session.query(Student.roll, Score.marks).join(Score, Score.student_id == Student.id).filter(Score.assessment_id == request.args.get("id", type=int))
    return jsonify({r: m for r, m in rows if m is not None})

@app.post("/api/scores")
@need("teacher", "admin")
def post_scores():
    d = request.get_json(silent=True) or {}
    a = db.session.get(Assessment, d.get("id"))
    if not a: return err("Unknown test", 404)
    for s in Student.query.filter(Student.roll.in_(list((d.get("scores") or {}).keys()))):
        raw = d["scores"][s.roll]; sc = db.session.get(Score, (a.id, s.id)) or Score(assessment_id=a.id, student_id=s.id)
        if raw in ("", None): sc.marks = None
        else:
            try: v = float(raw)
            except (TypeError, ValueError): return err(f"Marks for {s.roll} must be a number")
            if not 0 <= v <= a.max_marks: return err(f"Marks for {s.roll} must be between 0 and {a.max_marks:g}")
            sc.marks = v
        db.session.add(sc)
    log(f"Marks saved: test {a.id}"); db.session.commit()
    return jsonify(ok=True)

def clear_demo():
    ids = [s.id for s in Student.query.filter(Student.roll.like("DEMO%"))]
    if ids:
        for M in (Mark, Score, Note): M.query.filter(M.student_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.username.like("demo%"), User.role == "student").delete(synchronize_session=False)
        Student.query.filter(Student.id.in_(ids)).delete(synchronize_session=False)
    ClassSession.query.filter(~ClassSession.id.in_(db.session.query(Mark.session_id))).delete(synchronize_session=False)
    Assessment.query.filter(Assessment.name.in_(["UT1 (demo)", "UT2 (demo)"]), ~Assessment.id.in_(db.session.query(Score.assessment_id))).delete(synchronize_session=False)

@app.post("/api/demo")
@need("admin")
def demo():
    import random
    if not Slot.query.first(): load_default(); db.session.flush()
    clear_demo(); R = random.Random(42)
    F = ["Aarav", "Sneha", "Imran", "Divya", "Karthik", "Fatima", "Suresh", "Ananya", "Rahul", "Meghana", "Vikram", "Pooja", "Arjun", "Lakshmi", "Farhan"]
    Lm = ["Reddy", "Naidu", "Basha", "Rao", "Sharma", "Kumar"]
    kinds = ["good"] * 4 + ["avg"] * 2 + ["decl"] * 2 + ["chronic", "streak"]
    pw = generate_password_hash("demo1234"); studs = []
    for i in range(45):
        st = Student(roll=f"DEMO{i+1:02d}", name=f"{F[i % 15]} {Lm[(i // 15) * 2 + (i % 2)]}"); studs.append((st, kinds[i % 10]))
        db.session.add(st); db.session.add(User(username=st.roll.lower(), role="student", pw=pw, must_change=False))
    db.session.flush()
    end = date.today(); start = end - timedelta(days=41); hol = {h.day for h in Holiday.query}
    slots = Slot.query.filter(Slot.subject_id.isnot(None)).all()
    have = {(c.day, c.subject_id): c for c in ClassSession.query.filter(ClassSession.day >= start)}
    work = [start + timedelta(days=k) for k in range(42) if (start + timedelta(days=k)).weekday() != 6 and (start + timedelta(days=k)) not in hol]
    sess = []
    for day in work:
        per = {}
        for sl in slots:
            if sl.dow == day.weekday(): per[sl.subject_id] = per.get(sl.subject_id, 0) + sl.span
        for sid, p in per.items():
            cs = have.get((day, sid)) or ClassSession(day=day, subject_id=sid, periods=p)
            if cs.id is None: db.session.add(cs)
            sess.append((cs, day))
    db.session.flush(); last4 = set(work[-4:]); rows = []
    for st, kind in studs:
        for cs, day in sess:
            w = (day - start).days // 7
            p = {"good": .95, "avg": .86, "decl": .94 - .075 * w, "chronic": .6, "streak": .9}[kind]
            pres = False if (kind == "streak" and day in last4) else R.random() < p
            rows.append(Mark(session_id=cs.id, student_id=st.id, present=pres))
    db.session.bulk_save_objects(rows)
    ut = [start + timedelta(days=14), start + timedelta(days=35)]; ut = [d + timedelta(days=1) if d.weekday() == 6 else d for d in ut]
    for sub in Subject.query.all():
        if sub.name.endswith("Lab") or sub.name == "MM": continue
        for k, nm in enumerate(["UT1 (demo)", "UT2 (demo)"]):
            a = Assessment(subject_id=sub.id, name=nm, day=ut[k], max_marks=25); db.session.add(a); db.session.flush()
            for st, kind in studs:
                base = {"good": .8, "avg": .62, "decl": (.72, .36)[k], "chronic": .42, "streak": .6}[kind]
                m = round(max(0, min(25, (base + R.uniform(-.08, .08)) * 25)) * 2) / 2
                db.session.add(Score(assessment_id=a.id, student_id=st.id, marks=m))
    log("Demo data loaded"); db.session.commit()
    return jsonify(students=45)

@app.post("/api/demo/clear")
@need("admin")
def demo_clear():
    clear_demo(); log("Demo data removed"); db.session.commit()
    return jsonify(ok=True)

@app.post("/api/timetable/default")
@need("admin")
def tt_default():
    load_default(); log("Class timetable loaded"); db.session.commit()
    return jsonify(ok=True)

@app.get("/")
def index():
    return app.send_static_file("index.html")

with app.app_context():
    try:
        db.create_all()
        if not User.query.filter_by(role="admin").first():
            mk_user("admin", "admin", os.environ.get("ADMIN_PASSWORD", "admin123")); db.session.commit()
        if not Subject.query.first():
            load_default(); db.session.commit()
    except Exception as e:
        db.session.rollback(); print("init:", e)
