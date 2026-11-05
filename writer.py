# -*- coding: utf-8 -*-
"""
Написание текстов по запросу: рефераты и доклады по главам, а также эссе, рассказы, письма, стихи и т. д.
Результат сохраняется в «Документы\\Jarvis» (Word, а если нет python-docx, то обычный txt).
"""
import datetime
import os
import re

SYSTEM = ("Ты опытный автор учебных и научно-популярных текстов на русском языке. "
          "Пишешь грамотно, структурно, живым языком, без воды и без выдуманных фактов.")

# (регулярное выражение, название, пишем по главам?, слов на раздел/всего, инструкция)
KINDS = [
    (r"реферат", "реферат", True, 300, ""),
    (r"доклад", "доклад", True, 250, ""),
    (r"курсов", "курсовая работа", True, 350, ""),
    (r"эссе", "эссе", False, 350, "Напиши эссе: чёткий тезис, два-три аргумента с примерами и вывод."),
    (r"сочинени", "сочинение", False, 400, "Напиши школьное сочинение: вступление, основная часть, заключение."),
    (r"статью|статья", "статья", False, 450, "Напиши познавательную статью с небольшими абзацами."),
    (r"рассказ", "рассказ", False, 450, "Напиши рассказ с завязкой, развитием и развязкой, живым языком."),
    (r"сказк", "сказка", False, 400, "Напиши сказку."),
    (r"стих|стишок", "стихотворение", False, 130, "Напиши рифмованное стихотворение из 12-16 строк."),
    (r"поздравлени", "поздравление", False, 80, "Напиши тёплое поздравление."),
    (r"письмо", "письмо", False, 180, "Напиши письмо с уместным обращением и вежливым завершением."),
    (r"пост", "пост", False, 130, "Напиши короткий живой пост для социальных сетей."),
    (r"речь", "речь", False, 280, "Напиши речь для выступления."),
    (r"резюме", "резюме", False, 200, "Напиши резюме в деловом стиле."),
    (r"текст", "текст", False, 250, ""),
]
GENERIC = KINDS[-1]


def clean_md(text):
    t = re.sub(r"[*_`#]+", "", text)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def detect(args):
    """«реферат на тему архитектура школы» -> (вид, 'архитектура школы'). Вид None, если не нашли тему."""
    a = re.sub(r"^(мне|пожалуйста|нам)\s+", "", args.strip())
    spec, rest = GENERIC, a
    for k in KINDS:
        m = re.search(r"(?<!\w)(?:" + k[0] + r")\w*", a)
        if m and m.start() < 40:
            spec, rest = k, a[m.end():].strip()
            break
    topic = re.sub(r"^(на тему|по теме|тему|про|о том что|об|о)\s+", "", rest).strip()
    return (spec, topic) if topic else (None, "")


def generate(ask, spec, topic, sections, progress, cancelled):
    """
    ask(system, user, temperature, num_predict, timeout) -> текст.
    Возвращает список (заголовок, текст) или None, если пользователь отменил.
    """
    _, label, multi, words, instr = spec
    if not multi:
        text = ask(SYSTEM, f"{instr or 'Напиши текст.'} Тема: «{topic}». Объём около {words} слов. "
                           "Пиши на русском языке, без markdown и без пояснений до и после текста.",
                   0.8, words * 4, 600)
        return [(None, clean_md(text))]

    progress(f"Составляю план: «{label}» на тему «{topic}»")
    plan = ask(SYSTEM, f"Составь план для работы типа «{label}» на тему «{topic}». Выведи ровно {sections} "
                       "названий основных глав. Каждое название с новой строки, с номером, без пояснений "
                       "и без слов «Введение» и «Заключение».", 0.5, 250, 300)
    titles = []
    for line in plan.splitlines():
        m = re.match(r"^\s*(?:\d+[.)]|[-•*])\s*(.+?)\s*$", line)
        if m:
            t = re.sub(r"[*_`#]", "", m.group(1)).strip(" .:")
            if t and not re.match(r"(?i)^(введение|заключение)", t):
                titles.append(t)
    titles = titles[:sections]
    if len(titles) < 2:
        titles = ["Основные понятия", "История и развитие", "Современное состояние", "Перспективы"][:sections]

    plan_line = ", ".join(["Введение"] + titles + ["Заключение"])
    jobs = [("Введение", f"Напиши введение: актуальность темы, цель и задачи работы. Объём около {words // 2} слов.")]
    jobs += [(t, f"Напиши раздел «{t}». Объём около {words} слов.") for t in titles]
    jobs += [("Заключение", f"Напиши заключение: краткие выводы по всей работе. Объём около {words // 2} слов.")]

    parts = []
    for i, (title, task) in enumerate(jobs, 1):
        if cancelled():
            return None
        progress(f"Пишу «{label}»: раздел {i} из {len(jobs)}, {title}")
        text = ask(SYSTEM,
                   f"Работа типа «{label}» на тему «{topic}». План: {plan_line}.\n{task}\n"
                   "Пиши связным текстом на русском языке, абзацами, без списков, без markdown, без заголовка. "
                   "Не выдумывай точные цифры, даты, цитаты и названия источников.",
                   0.7, words * 4, 900)
        parts.append((title, clean_md(text)))
    return parts


def save_document(label, topic, parts, docs_dir):
    os.makedirs(docs_dir, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', " ", topic).strip()[:60] or "текст"
    base = os.path.join(docs_dir, f"{label} - {safe}")
    stamp = datetime.datetime.now().strftime("%H%M%S")
    title = topic[:1].upper() + topic[1:]
    try:
        from docx import Document
        from docx.shared import Cm, Pt, RGBColor
        path = base + ".docx"
        if os.path.exists(path):
            path = f"{base} {stamp}.docx"
        doc = Document()
        st = doc.styles["Normal"]
        st.font.name = "Times New Roman"
        st.font.size = Pt(14)

        def heading(text, level):
            h = doc.add_heading(text, level)
            for r in h.runs:
                r.font.name = "Times New Roman"
                r.font.color.rgb = RGBColor(0, 0, 0)

        heading(title, 0)
        for head, text in parts:
            if head:
                heading(head, 1)
            for p in text.split("\n"):
                if p.strip():
                    para = doc.add_paragraph(p.strip())
                    para.paragraph_format.first_line_indent = Cm(1.25)
                    para.paragraph_format.line_spacing = 1.5
        doc.save(path)
        return path
    except ImportError:
        path = base + ".txt"
        if os.path.exists(path):
            path = f"{base} {stamp}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(title + "\n\n")
            for head, text in parts:
                if head:
                    f.write(head + "\n\n")
                f.write(text + "\n\n")
        return path


# ======================================================================
#  Презентации (PowerPoint) и таблицы (Excel)
# ======================================================================
NUM_WORDS = {"один": 1, "одного": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
             "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
             "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15}

SLIDES_SYSTEM = ("Ты помощник, который готовит содержание презентаций на русском языке: коротко, по делу, "
                 "ёмкими тезисами, без воды и без выдуманных точных цифр.")


def parse_count(text, default, lo=3, hi=15):
    """«на 8 слайдов» / «на восемь слайдов» -> 8. Возвращает (число, текст без этой части)."""
    m = re.search(r"(?<!\w)(?:на|из)?\s*(\d+|[а-я]+)\s+слайд\w*", text)
    if m:
        w = m.group(1)
        n = int(w) if w.isdigit() else NUM_WORDS.get(w)
        if n:
            return max(lo, min(hi, n)), (text[:m.start()] + " " + text[m.end():]).strip()
    return default, text


def clean_topic(text):
    t = re.sub(r"^(таблицу|таблица|презентацию|презентация|слайды|в экселе|в эксель|в excel|в powerpoint|"
               r"в пауэрпойнте|пожалуйста|мне|нам|для меня)\s+", "", text.strip())
    t = re.sub(r"^(на тему|по теме|тему|про|о том что|об|о|для|с)\s+", "", t).strip()
    return t


def _titles(plan_text, count):
    titles = []
    for line in plan_text.splitlines():
        m = re.match(r"^\s*(?:\d+[.)]|[-•*])\s*(.+?)\s*$", line)
        if m:
            t = re.sub(r"[*_`#]", "", m.group(1)).strip(" .:")
            if t and not re.match(r"(?i)^(введение|заключение|выводы|спасибо)", t):
                titles.append(t[:70])
    return titles[:count]


def _bullets(text, limit=5):
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-•*–—])\s*", "", line)
        line = re.sub(r"[*_`#]", "", line).strip()
        if len(line) >= 4:
            out.append(line[:140])
    if len(out) < 2 and text.strip():          # модель написала сплошным текстом: режем на предложения
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", clean_md(text)) if len(x.strip()) >= 4]
        out = [x[:140] for x in sents]
    return out[:limit]


def generate_slides(ask, topic, count, progress, cancelled):
    """Возвращает список (заголовок слайда, [тезисы]) или None при отмене."""
    progress(f"Составляю план презентации на тему «{topic}»")
    plan = ask(SLIDES_SYSTEM, f"Составь план презентации на тему «{topic}»: ровно {count} названий слайдов, "
                              "каждое не длиннее шести слов. Каждое название с новой строки, с номером, "
                              "без пояснений, без слов «Введение», «Выводы» и «Спасибо».", 0.5, 250, 300)
    titles = _titles(plan, count)
    fallback = ["Что это такое", "История", "Основные особенности", "Применение", "Интересные факты",
                "Перспективы", "Проблемы", "Примеры"]
    while len(titles) < count:
        titles.append(fallback[len(titles) % len(fallback)])
    titles = titles[:count] + ["Выводы"]
    plan_line = "; ".join(titles)
    slides = []
    for i, title in enumerate(titles, 1):
        if cancelled():
            return None
        progress(f"Делаю слайд {i} из {len(titles)}: {title}")
        text = ask(SLIDES_SYSTEM,
                   f"Презентация на тему «{topic}». План слайдов: {plan_line}.\n"
                   f"Напиши содержание слайда «{title}»: 3-4 коротких тезиса, каждый с новой строки, "
                   "начиная с дефиса, не длиннее 12 слов. Без markdown и без пояснений.",
                   0.6, 250, 300)
        slides.append((title, _bullets(text) or ["Содержание уточняется"]))
    return slides


def save_pptx(topic, slides, docs_dir):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt

    DARK, ACCENT = RGBColor(0x1B, 0x26, 0x3B), RGBColor(0x2E, 0x86, 0xDE)
    LIGHT, TEXT, MUTED = RGBColor(0xF6, 0xF8, 0xFB), RGBColor(0x22, 0x2B, 0x38), RGBColor(0xB8, 0xC4, 0xD6)

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def rect(slide, x, y, w, h, color):
        r = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        r.fill.solid()
        r.fill.fore_color.rgb = color
        r.line.fill.background()
        r.shadow.inherit = False
        return r

    def box(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        return tf

    def style(run, size, color, bold=False):
        run.font.name = "Calibri"
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color

    # --- титульный слайд ---
    s = prs.slides.add_slide(blank)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = DARK
    rect(s, 0.9, 2.55, 0.12, 1.9, ACCENT)
    tf = box(s, 1.3, 2.4, 11, 2.2, MSO_ANCHOR.MIDDLE)
    r = tf.paragraphs[0].add_run()
    r.text = topic[:1].upper() + topic[1:]
    style(r, 44 if len(topic) < 45 else 34, RGBColor(255, 255, 255), True)
    tf2 = box(s, 1.3, 4.7, 11, 0.7)
    r = tf2.paragraphs[0].add_run()
    r.text = "Подготовлено Джарвисом · " + datetime.date.today().strftime("%d.%m.%Y")
    style(r, 18, MUTED)

    # --- слайды с содержанием ---
    for n, (title, bullets) in enumerate(slides, 1):
        s = prs.slides.add_slide(blank)
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = LIGHT
        rect(s, 0, 0, 0.3, 7.5, ACCENT)
        tf = box(s, 0.9, 0.45, 11.6, 1.2, MSO_ANCHOR.MIDDLE)
        r = tf.paragraphs[0].add_run()
        r.text = title
        style(r, 34 if len(title) < 40 else 28, DARK, True)
        rect(s, 0.95, 1.7, 1.4, 0.06, ACCENT)

        total = sum(len(b) for b in bullets)
        size = 26 if total < 220 else 22 if total < 340 else 19
        tf = box(s, 0.9, 2.0, 11.6, 4.8)
        for i, b in enumerate(bullets):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.space_after = Pt(14)
            pPr = para._p.get_or_add_pPr()
            pPr.set("marL", "342900")
            pPr.set("indent", "-342900")
            pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))
            run = para.add_run()
            run.text = b
            style(run, size, TEXT)
        num = box(s, 11.9, 6.85, 1.0, 0.4)
        num.paragraphs[0].add_run().text = str(n)
        style(num.paragraphs[0].runs[0], 12, MUTED)

    os.makedirs(docs_dir, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', " ", topic).strip()[:60] or "презентация"
    path = os.path.join(docs_dir, f"презентация - {safe}.pptx")
    if os.path.exists(path):
        path = os.path.join(docs_dir, f"презентация - {safe} {datetime.datetime.now():%H%M%S}.pptx")
    prs.save(path)
    return path


def generate_table(ask, topic, progress):
    """Возвращает (заголовки, строки)."""
    progress(f"Составляю таблицу на тему «{topic}»")
    text = ask("Ты помощник, который составляет аккуратные таблицы для Excel на русском языке.",
               f"Составь таблицу на тему «{topic}». Выведи ТОЛЬКО таблицу в формате CSV с разделителем «;»: "
               "первая строка с названиями столбцов, затем 8-12 строк данных. Столбцов от 3 до 6. "
               "Числа пиши без пробелов и без единиц измерения внутри ячеек (единицы укажи в заголовке столбца). "
               "Без markdown, без кавычек, без пояснений до и после.", 0.4, 700, 300)
    rows = []
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line or re.fullmatch(r"[\s|:\-;]+", line):
            continue
        if ";" in line:
            cells = line.split(";")
        elif "|" in line:
            cells = line.strip("|").split("|")
        elif "\t" in line:
            cells = line.split("\t")
        else:
            continue
        rows.append([c.strip().strip('"') for c in cells])
    if len(rows) < 2:
        raise ValueError("нейросеть не вернула таблицу, попробуйте сформулировать иначе")
    width = max(2, len(rows[0]))
    rows = [(r + [""] * width)[:width] for r in rows]
    return rows[0], rows[1:]


def _num(v):
    t = v.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    t = re.sub(r"(?<=\d)(?:руб\w*|₽|€|\$|%|шт\w*|кг|км)$", "", t)
    try:
        f = float(t)
        return int(f) if f.is_integer() and "." not in t else f
    except ValueError:
        return None


def save_xlsx(topic, headers, rows, docs_dir):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (re.sub(r"[\\/*?:\[\]]", " ", topic)[:28] or "Таблица").strip()
    thin = Side(style="thin", color="C9D1DC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", name="Calibri", size=12)
        c.fill = PatternFill("solid", fgColor="1B263B")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    ws.row_dimensions[1].height = 26

    numeric = [True] * len(headers)
    for r in rows:
        out = []
        for i, v in enumerate(r):
            n = _num(v) if v else None
            if n is None and v:
                numeric[i] = False
            out.append(n if n is not None else v)
        ws.append(out)
    n_rows = len(rows)

    # итоги считаем только там, где по заголовку это суммы, а не годы или номера
    money = re.compile(r"(?i)(сумм|стоим|цен|расход|доход|бюджет|руб|евро|€|\$|₽|количеств|шт|итог|затрат|оплат)")
    total_cols = [i for i, h in enumerate(headers)
                  if i > 0 and numeric[i] and money.search(h) and n_rows > 1]
    if total_cols:
        last = n_rows + 3                     # шапка + данные + пустая строка
        ws.cell(last, 1, "Итого")
        for i in total_cols:
            col = get_column_letter(i + 1)
            ws.cell(last, i + 1, f"=SUM({col}2:{col}{n_rows + 1})")
        for c in ws[last][:len(headers)]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E8EEF6")

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for c in row:
            if c.value is None and c.row != ws.max_row:
                continue
            c.border = border
            if isinstance(c.value, float) or (isinstance(c.value, str) and c.value.startswith("=")):
                c.number_format = "#,##0.00"
                c.alignment = Alignment(horizontal="right")
            elif isinstance(c.value, int):
                c.number_format = "0"
                c.alignment = Alignment(horizontal="right")
    for i in range(1, len(headers) + 1):
        letter = get_column_letter(i)
        longest = max((len(str(c.value)) for c in ws[letter] if c.value is not None and not str(c.value).startswith("=")),
                      default=8)
        ws.column_dimensions[letter].width = min(42, max(12, longest + 3))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{n_rows + 1}"

    os.makedirs(docs_dir, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', " ", topic).strip()[:60] or "таблица"
    path = os.path.join(docs_dir, f"таблица - {safe}.xlsx")
    if os.path.exists(path):
        path = os.path.join(docs_dir, f"таблица - {safe} {datetime.datetime.now():%H%M%S}.xlsx")
    wb.save(path)
    return path
