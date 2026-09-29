"""
Auroras Browser
Приватный браузер на Python (PyQt6 + QtWebEngine).
Стиль: минимализм, чёрно-белый, serif, в духе Nothing, с сиянием авроры на фоне.
Все иконки нарисованы вручную (QPainter), без системных шрифтов-эмодзи.

Установка:
    pip install PyQt6 PyQt6-WebEngine
Запуск:
    python auroras_browser.py

Если что-то пошло не так: программа больше не должна закрываться молча —
любая ошибка теперь показывается в отдельном окне и пишется в файл
~/.auroras_browser/crash.log — пришли содержимое этого файла, если что-то сломалось.
"""
import math
import os
import re
import sys
import traceback
from pathlib import Path
from urllib.parse import quote_plus

APP_NAME = "Auroras Browser"
APP_VERSION = "0.1 beta"
DATA_DIR = str(Path.home() / ".auroras_browser")
PROXY_FILE = os.path.join(DATA_DIR, "proxy.conf")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    pass

# --- Настройки приватности Chromium (до создания QApplication) ---
_chromium_flags = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",  # нет утечки IP через WebRTC
    "--disable-background-networking",
    "--disable-sync",
    "--disable-domain-reliability",
    "--disable-breakpad",
    "--no-pings",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
    "--enable-quic",
    "--enable-gpu-rasterization",
    # Известная причина подтормаживаний QtWebEngine на Windows: Chromium следит
    # за перекрытием окна и "притормаживает" вкладки, когда решает, что они не
    # видны — в самостоятельном (не системном) окне это определяется неверно.
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
]

# Шифрование трафика: честный и надёжный способ — не изобретать своё
# шифрование, а пускать весь трафик через прокси (например, Tor SOCKS5 на
# 127.0.0.1:9050). Tor сам делает и шифрование, и "раздвоение" — трафик идёт
# через несколько узлов (multi-hop), а не напрямую до сайта. Прокси Chromium
# понимает только при запуске, поэтому читаем сохранённый адрес заранее.
try:
    with open(PROXY_FILE, "r", encoding="utf-8") as f:
        _proxy_addr = f.read().strip()
except Exception:
    _proxy_addr = ""
if _proxy_addr:
    _chromium_flags.append(f"--proxy-server={_proxy_addr}")

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_chromium_flags)

from PyQt6.QtCore import qVersion, QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, QSize, QStandardPaths, Qt, QTimer, QUrl, QUrlQuery, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Глобальный перехват ошибок: раньше необработанное исключение внутри Qt
# (например, в обработчике кнопки) могло молча закрыть всё приложение без
# единого сообщения. Теперь любая ошибка: 1) пишется в crash.log,
# 2) выводится в консоль, 3) показывается всплывающим окном — и приложение
# не закрывается само.
# ---------------------------------------------------------------------------
def install_crash_handler():
    def hook(exctype, value, tb):
        text = "".join(traceback.format_exception(exctype, value, tb))
        print(text, file=sys.stderr)
        try:
            with open(os.path.join(DATA_DIR, "crash.log"), "a", encoding="utf-8") as f:
                f.write(text + "\n" + ("-" * 60) + "\n")
        except Exception:
            pass
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(f"{APP_NAME} — error")
            box.setText("Something went wrong. Details below (also saved to crash.log):")
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass

    sys.excepthook = hook


SEARCH_ENGINES = {
    "google": ("Google", "https://www.google.com/search?q="),
    "ddg": ("DuckDuckGo", "https://duckduckgo.com/?q="),
}

USER_AGENT = (  # актуальный десктопный Chrome UA — старые версии Google иногда
    # помечает как "небезопасный"/автоматизированный браузер
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Домены, которым нужны межсайтовые cookie для работы входа (Google OAuth
# использует cookie между accounts.google.com и другими google-доменами).
COOKIE_ALLOW_THIRD_PARTY = {
    "google.com", "gstatic.com", "googleusercontent.com", "duckduckgo.com",
}

QUICK_LINKS = [
    ("Gemini", "https://gemini.google.com"),
    ("GitHub", "https://github.com"),
    ("YouTube", "https://youtube.com"),
    ("PayPal", "https://paypal.com"),
    ("Reddit", "https://reddit.com"),
]

# Рекламные сети — блокируются на обоих уровнях защиты
ADS_DOMAINS = {
    "doubleclick.net", "adservice.google.com", "googlesyndication.com",
    "googleadservices.com", "criteo.com", "criteo.net", "taboola.com",
    "outbrain.com", "adnxs.com", "rubiconproject.com", "pubmatic.com",
    "openx.net", "moatads.com", "adsrvr.org", "bat.bing.com",
    "ads.linkedin.com", "static.ads-twitter.com", "snap.licdn.com",
}

# Аналитика / трекеры поведения — блокируются только на высоком уровне
TRACKER_DOMAINS = ADS_DOMAINS | {
    "google-analytics.com", "googletagmanager.com", "googletagservices.com",
    "facebook.net", "connect.facebook.net", "graph.facebook.com",
    "hotjar.com", "mixpanel.com", "segment.io", "segment.com", "amplitude.com",
    "scorecardresearch.com", "quantserve.com", "mc.yandex.ru", "an.yandex.ru",
    "top-fwz1.mail.ru", "counter.yadro.ru", "clarity.ms",
    "analytics.tiktok.com", "analytics.twitter.com", "fullstory.com",
    "mouseflow.com", "crazyegg.com", "newrelic.com", "bugsnag.com",
    "sentry-cdn.com", "app-measurement.com", "branch.io", "appsflyer.com",
    "adjust.com", "onetrust.com", "cookielaw.org",
}

IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Бренды, чьи имена чаще всего используют в фишинговых доменах
# (paypal-secure-login.info и т.п.). Проверка полностью локальная —
# без обращения к внешним сервисам, никаких данных никуда не уходит.
IMPERSONATION_BRANDS = [
    "google", "gmail", "paypal", "facebook", "instagram", "microsoft",
    "apple", "amazon", "github", "netflix", "whatsapp", "telegram",
    "binance", "coinbase", "bankofamerica", "wellsfargo", "chase",
    "steampowered", "discord",
]
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def assess_site_risk(url: "QUrl") -> list:
    """Локальные эвристики (без сети): что в адресе выглядит подозрительно.
    Это не полноценный антивирус — лишь простые признаки известных уловок."""
    reasons = []
    host = url.host().lower()
    if not host:
        return reasons

    if IP_RE.match(host):
        reasons.append("Address is a raw IP number instead of a domain name")

    authority = url.toString().split("://", 1)[-1].split("/", 1)[0]
    if "@" in authority:
        reasons.append("Address contains \"@\" — a classic trick to hide the real destination")

    if host.startswith("xn--") or ".xn--" in host:
        reasons.append("Domain uses punycode — may disguise look-alike letters as a real brand")

    if _CYRILLIC_RE.search(host) and _LATIN_RE.search(host):
        reasons.append("Domain mixes Latin and Cyrillic letters — a common look-alike trick")

    if host.count(".") >= 5:
        reasons.append("Unusually many sub-domains")

    for brand in IMPERSONATION_BRANDS:
        if brand in host.replace("-", "").replace("_", ""):
            official = host == f"{brand}.com" or host.endswith(f".{brand}.com")
            if not official:
                reasons.append(f"Contains \"{brand}\" but isn't the official {brand}.com domain")
                break

    return reasons


THEMES = {
    "dark": {"bg": "#000000", "fg": "#FFFFFF", "dim": "#8A8A8A", "line": "#2A2A2A"},
    "light": {"bg": "#FFFFFF", "fg": "#000000", "dim": "#7A7A7A", "line": "#D9D9D9"},
}

USER_AGREEMENT = """AURORAS BROWSER — USER AGREEMENT

By using Auroras Browser you agree to the following terms.

1. NATURE OF THE SOFTWARE. Auroras Browser is an independent desktop
application built on the Chromium engine (via PyQt6/QtWebEngine). It is not
affiliated with, endorsed by, or produced by Google LLC or any other browser
vendor. "Chrome" and "Google" are trademarks of their owners, referenced
only for compatibility.

2. NO WARRANTY. The software is provided "as is", without warranty of any
kind, express or implied, including merchantability, fitness for a
particular purpose, and non-infringement. You use it at your own risk. The
maintainer is not liable for direct, indirect, incidental, or consequential
damages arising from its use, including data loss or account restrictions
imposed by third-party websites.

3. PRIVACY AND LOCAL DATA. Auroras Browser runs no servers of its own and
does not collect, transmit, or sell browsing data, analytics, or telemetry.
Cookies, cache, and saved logins (when enabled in Settings) are stored only
on your own device and can be erased anytime with the Wipe button. An
optional proxy (e.g. Tor) routes traffic through a third party you choose;
that party's own privacy practices then apply.

4. THIRD-PARTY WEBSITES. Sites you visit (Google, GitHub, YouTube, PayPal,
Reddit, and others) are operated by their own providers under their own
terms and privacy policies. Some providers may restrict access from
non-standard or embedded browsers for security reasons — this is their
decision, not Auroras Browser's.

5. ACCEPTABLE USE. You agree not to use this browser for illegal activity,
to bypass security controls you are not authorized to bypass, or to violate
any site's terms of service. Tracker/ad blocking, proxy support, and
local-only storage are privacy tools, not a guarantee of anonymity or
immunity from any law.

6. CHANGES. This software, including its version number, may be modified at
any time by the person maintaining it. Continued use after changes means
you accept the updated terms.

7. CONTACT. This is an independent project with no formal support channel;
use, adapt, and modify it for personal, non-commercial purposes."""

# Простой скрипт автозаполнения логина/пароля через localStorage конкретного
# сайта (не системный автозаполнитель Chrome, а собственная реализация).
AUTOFILL_JS = r"""
(function() {
  if (window.__aurorasAutofillInstalled) return;
  window.__aurorasAutofillInstalled = true;

  function keyFor(host) { return "__auroras_cred__" + host; }

  function findPairs() {
    var pwds = document.querySelectorAll('input[type="password"]');
    var pairs = [];
    pwds.forEach(function(pwd) {
      var form = pwd.form || pwd.closest('form');
      var scope = form || document;
      var candidates = scope.querySelectorAll(
        'input[type="text"], input[type="email"], input:not([type])'
      );
      var user = candidates.length ? candidates[candidates.length - 1] : null;
      pairs.push({ user: user, pass: pwd, form: form });
    });
    return pairs;
  }

  function fill() {
    try {
      var saved = localStorage.getItem(keyFor(location.host));
      if (!saved) return;
      var data = JSON.parse(saved);
      findPairs().forEach(function(p) {
        if (p.user && data.user && !p.user.value) p.user.value = data.user;
        if (p.pass && data.pass && !p.pass.value) p.pass.value = data.pass;
      });
    } catch (e) {}
  }

  function save(p) {
    try {
      if (!p.pass || !p.pass.value) return;
      localStorage.setItem(keyFor(location.host), JSON.stringify({
        user: p.user ? p.user.value : "",
        pass: p.pass.value
      }));
    } catch (e) {}
  }

  function bind() {
    findPairs().forEach(function(p) {
      if (p.form && !p.form.__aurorasBound) {
        p.form.__aurorasBound = true;
        p.form.addEventListener('submit', function() { save(p); }, true);
      }
    });
    fill();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
  new MutationObserver(bind).observe(document.documentElement, { childList: true, subtree: true });
})();
"""


def is_in(host: str, domains: set) -> bool:
    """host совпадает с доменом из набора или является его поддоменом."""
    host = host.lower()
    while True:
        if host in domains:
            return True
        dot = host.find(".")
        if dot == -1:
            return False
        host = host[dot + 1:]


_RT = QWebEngineUrlRequestInfo.ResourceType
NAV_TYPES = {
    getattr(_RT, n) for n in ("ResourceTypeMainFrame", "ResourceTypeSubFrame") if hasattr(_RT, n)
}


def normalize_input(text: str, search_url: str) -> QUrl:
    text = text.strip()
    if not text:
        return QUrl()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return QUrl(text)
    if " " not in text and ("." in text or text.startswith("localhost")):
        scheme = "http://" if text.startswith("localhost") else "https://"
        return QUrl(scheme + text)
    return QUrl(search_url + quote_plus(text))


def extract_search_query(url: QUrl):
    """Если страница — выдача Google/DuckDuckGo, возвращает текст запроса, иначе None."""
    host = url.host().lower()
    path = url.path()
    is_google = re.match(r"^(www\.)?google\.[a-z.]+$", host) and path == "/search"
    is_ddg = host.endswith("duckduckgo.com") and path == "/"
    if not (is_google or is_ddg):
        return None
    q = QUrlQuery(url).queryItemValue("q", QUrl.ComponentFormattingOption.FullyDecoded)
    return q or None


def newtab_html(theme: str, search_url: str) -> str:
    t = THEMES[theme]
    tiles = "".join(
        f'<a class="tile" href="{url}"><span>{name}</span></a>' for name, url in QUICK_LINKS
    )
    if theme == "dark":
        aurora_opacity = "0.55"
        a1, a2, a3, a4 = "#ffffff", "#c9c9c9", "#9a9a9a", "#ffffff"
    else:
        # на белом фоне светлое сияние не видно — используем тёмные оттенки
        aurora_opacity = "0.42"
        a1, a2, a3, a4 = "#000000", "#4a4a4a", "#7a7a7a", "#000000"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>New tab</title>
<style>
  html,body{{height:100%;margin:0;background:{t['bg']};color:{t['fg']};
    font-family:Georgia,'Times New Roman',serif;overflow:hidden}}
  .aurora{{position:fixed;inset:-20%;will-change:transform;opacity:{aurora_opacity};
    background:
      radial-gradient(38% 32% at 20% 25%, {a1} 0%, transparent 70%),
      radial-gradient(30% 26% at 75% 20%, {a2} 0%, transparent 70%),
      radial-gradient(34% 30% at 55% 75%, {a3} 0%, transparent 70%),
      radial-gradient(26% 24% at 85% 70%, {a4} 0%, transparent 70%);
    animation:drift 22s ease-in-out infinite alternate;}}
  @keyframes drift{{
    0%{{transform:translate(0,0) rotate(0deg) scale(1);}}
    100%{{transform:translate(-3%,4%) rotate(8deg) scale(1.08);}}
  }}
  @media (prefers-reduced-motion:reduce){{.aurora{{animation:none}}}}
  .wrap{{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;
    align-items:center;justify-content:center}}
  h1{{font-weight:400;font-size:64px;letter-spacing:.04em;margin:0 0 6px}}
  .sub{{color:{t['dim']};font-size:12px;letter-spacing:.35em;text-transform:uppercase;margin-bottom:40px}}
  input{{width:460px;max-width:80vw;background:transparent;color:{t['fg']};border:1px solid {t['fg']};
    border-radius:999px;padding:14px 22px;font:inherit;font-size:16px;outline:none;text-align:center}}
  input::placeholder{{color:{t['dim']}}}
  .tiles{{display:flex;gap:14px;margin-top:38px;flex-wrap:wrap;justify-content:center;max-width:520px}}
  .tile{{border:1px solid {t['line']};border-radius:999px;padding:9px 18px;color:{t['fg']};
    text-decoration:none;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
    transition:border-color .15s ease}}
  .tile:hover{{border-color:{t['fg']}}}
</style></head><body>
  <div class="aurora"></div>
  <div class="wrap">
    <h1>Auroras</h1>
    <div class="sub">Private by default</div>
    <form onsubmit="location.href='{search_url}'+encodeURIComponent(this.q.value);return false;">
      <input name="q" placeholder="Search or enter address" autofocus autocomplete="off">
    </form>
    <div class="tiles">{tiles}</div>
  </div>
</body></html>"""


def stylesheet(theme: str) -> str:
    t = THEMES[theme]
    return f"""
    QMainWindow, QWidget {{
        background: {t['bg']}; color: {t['fg']};
        font-family: Georgia, 'Times New Roman', serif; font-size: 14px;
    }}
    QToolButton {{
        background: transparent; border: 1px solid {t['line']}; border-radius: 16px;
        min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
    }}
    QToolButton:hover {{ border-color: {t['fg']}; }}
    QToolButton:pressed {{ background: {t['line']}; }}
    QLineEdit {{
        background: transparent; color: {t['fg']};
        border: 1px solid {t['line']}; border-radius: 16px;
        padding: 6px 16px; selection-background-color: {t['fg']};
        selection-color: {t['bg']};
    }}
    QLineEdit:focus {{ border-color: {t['fg']}; }}
    QLabel#shield {{ color: {t['dim']}; font-size: 11px; letter-spacing: 2px; padding: 0 6px; }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {t['line']}; }}
    QTabBar {{ background: {t['bg']}; }}
    QTabBar::tab {{
        background: {t['bg']}; color: {t['dim']}; padding: 9px 18px;
        border: none; border-bottom: 2px solid transparent;
        font-size: 11px; letter-spacing: 2px; min-width: 90px; max-width: 200px;
    }}
    QTabBar::tab:selected {{ color: {t['fg']}; border-bottom: 2px solid {t['fg']}; }}
    QTabBar::tab:hover {{ color: {t['fg']}; }}
    QProgressBar {{ background: transparent; border: none; max-height: 2px; min-height: 2px; }}
    QProgressBar::chunk {{ background: {t['fg']}; }}
    QDialog {{ background: {t['bg']}; }}
    QComboBox {{
        background: transparent; color: {t['fg']}; border: 1px solid {t['line']};
        border-radius: 10px; padding: 5px 10px;
    }}
    QComboBox QAbstractItemView {{
        background: {t['bg']}; color: {t['fg']}; selection-background-color: {t['line']};
    }}
    QCheckBox {{ color: {t['fg']}; }}
    QPushButton {{
        background: transparent; color: {t['fg']}; border: 1px solid {t['fg']};
        border-radius: 12px; padding: 6px 16px; letter-spacing: 1px;
    }}
    QPushButton:hover {{ background: {t['fg']}; color: {t['bg']}; }}
    QWidget#appBanner {{ background: {t['line']}; }}
    QWidget#appBanner QLabel {{ font-size: 12px; }}
    """


# ---------------------------------------------------------------------------
# Иконки: рисуются вручную через QPainter (векторно), не зависят от системных
# эмодзи-шрифтов, поэтому выглядят одинаково на любой ОС.
# ---------------------------------------------------------------------------
def make_icon(kind: str, color: str, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = size

    if kind == "back":
        p.drawLine(QPointF(r * 0.60, r * 0.22), QPointF(r * 0.30, r * 0.50))
        p.drawLine(QPointF(r * 0.30, r * 0.50), QPointF(r * 0.60, r * 0.78))
    elif kind == "forward":
        p.drawLine(QPointF(r * 0.40, r * 0.22), QPointF(r * 0.70, r * 0.50))
        p.drawLine(QPointF(r * 0.70, r * 0.50), QPointF(r * 0.40, r * 0.78))
    elif kind == "reload":
        rect = QRectF(r * 0.22, r * 0.22, r * 0.56, r * 0.56)
        p.drawArc(rect, 40 * 16, 280 * 16)
        cx, cy = r * 0.78, r * 0.34
        head = QPainterPath()
        head.moveTo(cx, cy - r * 0.11)
        head.lineTo(cx + r * 0.11, cy)
        head.lineTo(cx - r * 0.02, cy + r * 0.10)
        head.closeSubpath()
        p.setBrush(QColor(color))
        p.drawPath(head)
    elif kind == "home":
        path = QPainterPath()
        path.moveTo(r * 0.5, r * 0.20)
        path.lineTo(r * 0.78, r * 0.44)
        path.lineTo(r * 0.78, r * 0.80)
        path.lineTo(r * 0.22, r * 0.80)
        path.lineTo(r * 0.22, r * 0.44)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "wipe":
        p.drawLine(QPointF(r * 0.28, r * 0.28), QPointF(r * 0.72, r * 0.72))
        p.drawLine(QPointF(r * 0.72, r * 0.28), QPointF(r * 0.28, r * 0.72))
    elif kind == "theme":
        p.drawEllipse(QRectF(r * 0.24, r * 0.24, r * 0.52, r * 0.52))
        p.setBrush(QColor(color))
        p.drawPie(QRectF(r * 0.24, r * 0.24, r * 0.52, r * 0.52), 90 * 16, 180 * 16)
    elif kind == "settings":
        cx, cy = r * 0.5, r * 0.5
        teeth, body_r, tip_r, hole_r = 8, r * 0.20, r * 0.34, r * 0.12
        gear = QPainterPath()
        steps = teeth * 2
        for i in range(steps + 1):
            a = i * (2 * math.pi / steps)
            rad = tip_r if i % 2 == 0 else body_r
            x, y = cx + math.cos(a) * rad, cy + math.sin(a) * rad
            gear.moveTo(x, y) if i == 0 else gear.lineTo(x, y)
        gear.closeSubpath()
        gear.addEllipse(QPointF(cx, cy), hole_r, hole_r)
        gear.setFillRule(Qt.FillRule.OddEvenFill)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(gear)
    elif kind == "plus":
        p.drawLine(QPointF(r * 0.5, r * 0.24), QPointF(r * 0.5, r * 0.76))
        p.drawLine(QPointF(r * 0.24, r * 0.5), QPointF(r * 0.76, r * 0.5))
    elif kind == "download":
        p.drawLine(QPointF(r * 0.5, r * 0.18), QPointF(r * 0.5, r * 0.56))
        arrow = QPainterPath()
        arrow.moveTo(r * 0.32, r * 0.42)
        arrow.lineTo(r * 0.5, r * 0.62)
        arrow.lineTo(r * 0.68, r * 0.42)
        p.drawPath(arrow)
        p.drawLine(QPointF(r * 0.22, r * 0.80), QPointF(r * 0.78, r * 0.80))
    p.end()
    return QIcon(pm)


ICON_TOOLTIPS = {
    "back": "Назад",
    "forward": "Вперёд",
    "reload": "Обновить",
    "home": "Главная",
    "wipe": "Стереть все данные сейчас",
    "theme": "Тема: тёмная / светлая",
    "settings": "Настройки",
    "plus": "Новая вкладка (Ctrl+T)",
    "download": "Загрузки",
}


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def unique_path(path: str) -> str:
    """Если файл с таким именем уже есть в Загрузках — добавляет " (1)", " (2)" и т.д."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{base} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


class Settings:
    """Настройки браузера (хранятся только в памяти процесса, кроме proxy — см. PROXY_FILE)."""

    def __init__(self):
        self.search_engine = "google"          # 'google' | 'ddg'
        self.protection = "high"                # 'medium' | 'high'
        self.remember_logins = False            # сохранять cookie/сессии/автозаполнение на диске
        self.proxy = _proxy_addr                # адрес SOCKS5/HTTP-прокси (напр. Tor), применяется после перезапуска

    @property
    def search_url(self) -> str:
        return SEARCH_ENGINES[self.search_engine][1]

    @property
    def tracker_set(self) -> set:
        return TRACKER_DOMAINS if self.protection == "high" else ADS_DOMAINS


class AgreementDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Agreement")
        self.resize(520, 480)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(USER_AGREEMENT)
        layout.addWidget(text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)


class SettingsDialog(QDialog):
    def __init__(self, browser: "Browser"):
        super().__init__(browser)
        self.browser = browser
        self.setWindowTitle("Settings")
        self.setMinimumWidth(380)

        form = QFormLayout()

        self.engine_box = QComboBox()
        for key, (label, _url) in SEARCH_ENGINES.items():
            self.engine_box.addItem(label, key)
        self.engine_box.setCurrentIndex(
            list(SEARCH_ENGINES.keys()).index(browser.settings.search_engine)
        )
        form.addRow("Search engine", self.engine_box)

        self.protection_box = QComboBox()
        self.protection_box.addItem("Medium — block ads only", "medium")
        self.protection_box.addItem("High — block ads & trackers", "high")
        self.protection_box.setCurrentIndex(0 if browser.settings.protection == "medium" else 1)
        form.addRow("Protection level", self.protection_box)

        self.remember_box = QCheckBox("Save passwords && remember signed-in accounts")
        self.remember_box.setChecked(browser.settings.remember_logins)
        form.addRow(self.remember_box)

        note = QLabel(
            "When enabled, login sessions, cookies and saved form logins are\n"
            "kept on disk between visits and app restarts, so accounts like\n"
            "Gemini stay signed in and saved logins are refilled automatically.\n"
            "When disabled, everything is wiped when the browser closes."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow(note)

        self.proxy_edit = QLineEdit(browser.settings.proxy)
        self.proxy_edit.setPlaceholderText("e.g. 127.0.0.1:9050 for Tor — leave empty to disable")
        form.addRow("Proxy (encrypted, multi-hop)", self.proxy_edit)

        proxy_note = QLabel(
            "Real traffic encryption isn't something a browser can safely fake —\n"
            "route through Tor's SOCKS proxy instead: it encrypts and relays your\n"
            "traffic through several hops before it reaches a site. Requires\n"
            "restarting the browser after saving to take effect."
        )
        proxy_note.setWordWrap(True)
        proxy_note.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow(proxy_note)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(f"Auroras Browser — version {APP_VERSION}"))
        version_row.addStretch(1)
        terms_btn = QPushButton("User Agreement")
        terms_btn.clicked.connect(self._show_agreement)
        version_row.addWidget(terms_btn)
        form.addRow(version_row)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _show_agreement(self):
        AgreementDialog(self).exec()

    def apply(self):
        s = self.browser.settings
        s.search_engine = self.engine_box.currentData()
        s.protection = self.protection_box.currentData()
        s.remember_logins = self.remember_box.isChecked()

        proxy = self.proxy_edit.text().strip()
        restart_needed = proxy != s.proxy
        s.proxy = proxy
        try:
            if proxy:
                with open(PROXY_FILE, "w", encoding="utf-8") as f:
                    f.write(proxy)
            elif os.path.exists(PROXY_FILE):
                os.remove(PROXY_FILE)
        except Exception:
            traceback.print_exc()
        if restart_needed:
            QMessageBox.information(
                self, "Proxy",
                "Proxy setting saved. Restart Auroras Browser for it to take effect.",
            )


class Stats(QObject):
    blocked = pyqtSignal()


class PrivacyInterceptor(QWebEngineUrlRequestInterceptor):
    """Блокирует рекламу/трекеры (по уровню защиты), апгрейдит HTTP -> HTTPS, шлёт DNT/GPC."""

    def __init__(self, stats: Stats, settings: Settings):
        super().__init__()
        self.stats = stats
        self.settings = settings
        self._verdicts = {}

    def interceptRequest(self, info):
        # Этот метод вызывается на КАЖДЫЙ запрос страницы — держим его максимально лёгким.
        try:
            url = info.requestUrl()
            host = url.host()

            key = (host, self.settings.protection)
            blocked = self._verdicts.get(key)
            if blocked is None:
                if len(self._verdicts) > 5000:
                    self._verdicts.clear()
                blocked = self._verdicts[key] = is_in(host, self.settings.tracker_set)
            if blocked:
                info.block(True)
                self.stats.blocked.emit()
                return

            if url.scheme() == "http":
                h = host.lower()
                local = h in ("localhost", "127.0.0.1") or h.endswith(".local") or IP_RE.match(h)
                if not local:
                    secure = QUrl(url)
                    secure.setScheme("https")
                    info.redirect(secure)
                    return

            # DNT / GPC нужны только на запросах самих страниц, не на каждой картинке.
            if not NAV_TYPES or info.resourceType() in NAV_TYPES:
                info.setHttpHeader(b"DNT", b"1")
                info.setHttpHeader(b"Sec-GPC", b"1")
        except Exception:
            # Никогда не даём ошибке в перехватчике сети уронить всё приложение.
            pass


class Page(QWebEnginePage):
    def __init__(self, profile, open_tab, browser, parent=None):
        super().__init__(profile, parent)
        self._open_tab = open_tab
        self._browser = browser
        try:
            self.permissionRequested.connect(lambda perm: perm.deny())
        except AttributeError:
            self.featurePermissionRequested.connect(self._deny_feature)

    def _deny_feature(self, origin, feature):
        self.setFeaturePermission(
            origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )

    def createWindow(self, _type):
        return self._open_tab().page()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        # Схемы вроде mailto:/tg:/skype:/zoommtg: — движок их не умеет
        # загрузить как страницу. Вместо ошибки — предлагаем открыть приложением.
        scheme = url.scheme()
        if scheme not in ("http", "https", "about", "data", "blob", "qrc", "file"):
            try:
                self._browser.offer_external_open(url)
            except Exception:
                pass
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class SiteWarningOverlay(QWidget):
    """Затемняет экран и показывает причины, почему сайт выглядит подозрительно.
    Проверка чисто локальная (по виду адреса) — это не сканер вредоносного кода."""

    def __init__(self, browser: "Browser"):
        super().__init__(browser)
        self.browser = browser
        self._view = None
        self._url = None
        self.hide()

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QWidget()
        self.card.setObjectName("warningCard")
        self.card.setMaximumWidth(460)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(36, 30, 36, 26)
        card_layout.setSpacing(14)

        self.title = QLabel("⚠  This site looks suspicious")
        self.title.setStyleSheet("font-size: 19px; letter-spacing: 1px;")
        self.title.setWordWrap(True)

        self.reasons_label = QLabel("")
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setStyleSheet("font-size: 12px;")

        disclaimer = QLabel(
            "Based on how the address looks — not a full malware scan."
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("font-size: 10px; color: palette(mid);")

        btn_row = QHBoxLayout()
        back_btn = QPushButton("Go back to safety")
        cont_btn = QPushButton("Continue anyway")
        back_btn.clicked.connect(self._go_back)
        cont_btn.clicked.connect(self._continue_anyway)
        btn_row.addWidget(back_btn)
        btn_row.addWidget(cont_btn)

        card_layout.addWidget(self.title)
        card_layout.addWidget(self.reasons_label)
        card_layout.addWidget(disclaimer)
        card_layout.addLayout(btn_row)
        outer.addWidget(self.card)

    def show_for(self, view, url, reasons):
        self._view = view
        self._url = url
        self.reasons_label.setText("\n".join(f"•  {r}" for r in reasons))

        t = THEMES[self.browser.theme]
        veil = "rgba(0, 0, 0, 225)" if self.browser.theme == "dark" else "rgba(255, 255, 255, 235)"
        self.setStyleSheet(f"background: {veil};")
        self.card.setStyleSheet(
            f"#warningCard {{ background: {t['bg']}; color: {t['fg']}; "
            f"border: 1px solid {t['fg']}; border-radius: 12px; }}"
        )
        self.setGeometry(self.browser.rect())
        self.show()
        self.raise_()

    def _go_back(self):
        view = self._view
        self.hide()
        if view is None:
            return
        try:
            if view.history().canGoBack():
                view.back()
            else:
                self.browser.show_home(view)
        except Exception:
            pass

    def _continue_anyway(self):
        if self._url is not None:
            self.browser._risk_allowed.add(self._url.host().lower())
        self.hide()


def telegram_deep_link(url: "QUrl") -> str:
    """Превращает https://t.me/... в tg://... — ссылку для открытия в приложении Telegram."""
    path = url.path().lstrip("/")
    if not path:
        return "tg://"
    if path.startswith("+"):
        return f"tg://join?invite={path[1:]}"
    if path.startswith("joinchat/"):
        return f"tg://join?invite={path[len('joinchat/'):]}"
    parts = path.split("/")
    username = parts[0]
    link = f"tg://resolve?domain={username}"
    if len(parts) > 1 and parts[1].isdigit():
        link += f"&post={parts[1]}"
    return link


def whatsapp_deep_link(url: "QUrl") -> str:
    phone = url.path().lstrip("/")
    text = QUrlQuery(url).queryItemValue("text", QUrl.ComponentFormattingOption.FullyDecoded)
    link = f"whatsapp://send?phone={phone}" if phone else "whatsapp://send"
    if text:
        link += ("&" if "?" in link else "?") + f"text={quote_plus(text)}"
    return link


def discord_deep_link(url: "QUrl") -> str:
    path = url.path().lstrip("/")
    if url.host().endswith("discord.com") and path.startswith("invite/"):
        code = path[len("invite/"):]
    else:
        code = path
    return f"discord://-/invite/{code}" if code else "discord://-"


def spotify_deep_link(url: "QUrl") -> str:
    parts = url.path().lstrip("/").split("/")
    if len(parts) >= 2:
        return f"spotify:{parts[0]}:{parts[1]}"
    return "spotify:"


def zoom_deep_link(url: "QUrl") -> str:
    parts = url.path().lstrip("/").split("/")
    meeting_id = parts[1] if len(parts) >= 2 and parts[0] == "j" else (parts[0] if parts else "")
    pwd = QUrlQuery(url).queryItemValue("pwd")
    link = f"zoommtg://zoom.us/join?confno={meeting_id}"
    if pwd:
        link += f"&pwd={pwd}"
    return link


# Сайты, у которых есть свой "родной" протокол приложения — при переходе на
# такую ссылку показываем баннер "открыть в приложении X" (не автоматически).
# host-проверка + функция, превращающая https-ссылку в deep link приложения.
EXTERNAL_LINK_RULES = [
    (lambda h: h == "t.me" or h.endswith(".t.me"), "Telegram", telegram_deep_link),
    (lambda h: h == "wa.me" or h.endswith(".wa.me"), "WhatsApp", whatsapp_deep_link),
    (lambda h: h in ("discord.gg", "discord.com") or h.endswith(".discord.gg"), "Discord", discord_deep_link),
    (lambda h: h == "open.spotify.com", "Spotify", spotify_deep_link),
    (lambda h: h.endswith("zoom.us"), "Zoom", zoom_deep_link),
]

# Прямые схемы-ссылки (mailto:, tg:, skype: и т.д.) — движок не умеет их
# загрузить как страницу, поэтому просто красиво называем приложение в баннере.
SCHEME_APP_NAMES = {
    "mailto": "Mail", "tel": "Phone", "sms": "Messages", "skype": "Skype",
    "zoommtg": "Zoom", "spotify": "Spotify", "discord": "Discord",
    "slack": "Slack", "steam": "Steam", "tg": "Telegram", "whatsapp": "WhatsApp",
    "viber": "Viber", "ms-teams": "Teams", "ftp": "FTP client",
}


class DownloadRow(QWidget):
    def __init__(self, filename: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(4)

        self.name_label = QLabel(filename)
        self.name_label.setStyleSheet("font-size: 12px;")
        self.name_label.setWordWrap(True)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)

        self.status = QLabel("Starting…")
        self.status.setStyleSheet("font-size: 10px; color: palette(mid);")

        layout.addWidget(self.name_label)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)


class DownloadsPanel(QWidget):
    """Всплывающая панель со списком загрузок и прогрессом каждой."""

    def __init__(self, browser: "Browser"):
        super().__init__(browser, Qt.WindowType.Popup)
        self.browser = browser
        self.setMinimumWidth(300)
        self.setMaximumWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        title = QLabel("DOWNLOADS")
        title.setStyleSheet("font-size: 10px; letter-spacing: 2px; color: palette(mid);")
        outer.addWidget(title)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(2)
        outer.addLayout(self.list_layout)

        self.empty_label = QLabel("No downloads yet")
        self.empty_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        self.list_layout.addWidget(self.empty_label)

    def add_row(self, filename: str) -> DownloadRow:
        self.empty_label.hide()
        row = DownloadRow(filename)
        self.list_layout.insertWidget(0, row)
        return row

    def show_below(self, button: QToolButton):
        t = THEMES[self.browser.theme]
        self.setStyleSheet(
            f"background: {t['bg']}; color: {t['fg']}; "
            f"border: 1px solid {t['line']}; border-radius: 10px;"
        )
        pos = button.mapToGlobal(button.rect().bottomRight())
        self.adjustSize()
        self.move(pos.x() - self.width(), pos.y() + 6)
        self.show()


class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = "dark"
        self.blocked_count = 0
        self.settings = Settings()
        self.icon_buttons = {}
        self._autofill_script = None
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)

        self._setup_profile()
        self._setup_ui()
        self._setup_shortcuts()
        self.apply_theme()
        self.new_tab()

    # ---------- Профиль (общий для всех вкладок) ----------
    def _setup_profile(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(DATA_DIR + "/cache", exist_ok=True)

        self.profile = QWebEngineProfile("Auroras", self)
        self.profile.setPersistentStoragePath(DATA_DIR)
        self.profile.setCachePath(DATA_DIR + "/cache")
        self.profile.setHttpCacheMaximumSize(512 * 1024 * 1024)
        # Реальная версия Chromium из установленного PyQt6-WebEngine, без токена
        # "QtWebEngine/..". Выдуманная версия хуже: Google сверяет UA с возможностями.
        default_ua = self.profile.httpUserAgent()
        clean_ua = re.sub(r"\s*QtWebEngine/[\d.]+", "", default_ua)
        self.profile.setHttpUserAgent(clean_ua or USER_AGENT)
        print("Chromium UA:", clean_ua)
        self._apply_cookie_policy()
        self._update_autofill_script()

        self.stats = Stats()
        self.stats.blocked.connect(self._on_blocked)
        self.interceptor = PrivacyInterceptor(self.stats, self.settings)
        self.profile.setUrlRequestInterceptor(self.interceptor)

        def cookie_filter(req):
            if not req.thirdParty:
                return True
            hosts = (req.origin.host().lower(), req.firstPartyUrl.host().lower())
            return any(is_in(h, COOKIE_ALLOW_THIRD_PARTY) for h in hosts)

        self.profile.cookieStore().setCookieFilter(cookie_filter)

        s = self.profile.settings()
        for name, value in [
            ("WebRTCPublicInterfacesOnly", True),
            ("PluginsEnabled", False),
            ("ScreenCaptureEnabled", False),
            ("HyperlinkAuditingEnabled", False),
            ("AutoLoadIconsForPage", False),
            ("JavascriptCanAccessClipboard", False),
            ("LocalContentCanAccessRemoteUrls", False),
            ("FullScreenSupportEnabled", True),
            ("LocalStorageEnabled", True),
        ]:
            attr = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attr is not None:
                s.setAttribute(attr, value)

        self.profile.downloadRequested.connect(self._on_download)

    def _apply_cookie_policy(self):
        policy = (
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
            if self.settings.remember_logins
            else QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.profile.setPersistentCookiesPolicy(policy)

    def _update_autofill_script(self):
        """Включает/выключает скрипт автозаполнения. QWebEngineScriptCollection в
        PyQt6 не даёт искать скрипт по имени напрямую, поэтому храним ссылку сами."""
        scripts = self.profile.scripts()
        if self._autofill_script is not None:
            try:
                scripts.remove(self._autofill_script)
            except Exception:
                pass
            self._autofill_script = None
        if self.settings.remember_logins:
            script = QWebEngineScript()
            script.setName("auroras-autofill")
            script.setSourceCode(AUTOFILL_JS)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(True)
            scripts.insert(script)
            self._autofill_script = script

    # ---------- Интерфейс ----------
    def _setup_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)

        self.btn_back = self._icon_btn("back", self._safe(lambda: self.current().back()))
        self.btn_fwd = self._icon_btn("forward", self._safe(lambda: self.current().forward()))
        self.btn_reload = self._icon_btn("reload", self._safe(lambda: self.current().reload()))
        self.btn_home = self._icon_btn("home", self._safe(self.go_home))

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Search or enter address")
        self.url_bar.returnPressed.connect(self._safe(self.navigate))

        self.shield = QLabel("● 0 BLOCKED")
        self.shield.setObjectName("shield")
        self.shield.setToolTip("Заблокировано за сессию")

        self.btn_wipe = self._icon_btn("wipe", self._safe(self.wipe))
        self.btn_theme = self._icon_btn("theme", self._safe(self.toggle_theme))
        self.btn_downloads = self._icon_btn("download", self._safe(self.toggle_downloads_panel))
        self.btn_settings = self._icon_btn("settings", self._safe(self.open_settings))

        for w in (self.btn_back, self.btn_fwd, self.btn_reload, self.btn_home):
            row.addWidget(w)
        row.addWidget(self.url_bar, 1)
        row.addWidget(self.shield)
        for w in (self.btn_wipe, self.btn_theme, self.btn_downloads, self.btn_settings):
            row.addWidget(w)
        root.addWidget(bar)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)

        # Полоса-уведомление "это можно открыть в приложении" (например, Telegram).
        self.app_banner = QWidget()
        self.app_banner.setObjectName("appBanner")
        self.app_banner.hide()
        banner_row = QHBoxLayout(self.app_banner)
        banner_row.setContentsMargins(16, 8, 12, 8)
        self.app_banner_label = QLabel("")
        self.app_banner_label.setWordWrap(True)
        app_open_btn = QPushButton("Open")
        app_open_btn.clicked.connect(self._safe(self._open_external_app))
        app_dismiss_btn = QToolButton()
        app_dismiss_btn.setText("✕")
        app_dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        app_dismiss_btn.clicked.connect(self._safe(lambda: self.app_banner.hide()))
        banner_row.addWidget(self.app_banner_label, 1)
        banner_row.addWidget(app_open_btn)
        banner_row.addWidget(app_dismiss_btn)
        root.addWidget(self.app_banner)
        self._pending_deep_link = None

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._safe(self.close_tab))
        self.tabs.currentChanged.connect(self._safe(self._on_tab_changed))
        # "+" живёт рядом со вкладками, как в обычных браузерах — не в тулбаре.
        self.btn_new = self._icon_btn("plus", self._safe(lambda: self.new_tab()))
        self.tabs.setCornerWidget(self.btn_new, Qt.Corner.TopRightCorner)
        root.addWidget(self.tabs, 1)

        self.find_bar = QLineEdit()
        self.find_bar.setPlaceholderText("Find in page  •  Enter — next  •  Esc — close")
        self.find_bar.hide()
        self.find_bar.textChanged.connect(self._safe(lambda t: self.current().findText(t)))
        self.find_bar.returnPressed.connect(
            self._safe(lambda: self.current().findText(self.find_bar.text()))
        )
        root.addWidget(self.find_bar)

        self.setCentralWidget(central)

        self._pending_home = set()
        self.busy = QLabel(self)
        self.busy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.busy.hide()

        self._risk_allowed = set()
        self.warning_overlay = SiteWarningOverlay(self)

        self._active_downloads = 0
        self._pulse_anim = None
        self.downloads_panel = DownloadsPanel(self)

    @staticmethod
    def _safe(fn):
        """Оборачивает обработчик, чтобы ошибка внутри не рушила всё окно."""
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                traceback.print_exc()
        return wrapper

    def _icon_btn(self, kind: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIconSize(QSize(18, 18))
        b.setToolTip(ICON_TOOLTIPS[kind])
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        # Qt передаёт clicked(checked: bool) — если это не отбросить явно,
        # обработчик без параметров падает с TypeError на каждый клик.
        b.clicked.connect(lambda checked=False, _fn=slot: _fn())
        self.icon_buttons[kind] = b
        return b

    def _refresh_icons(self):
        fg = THEMES[self.theme]["fg"]
        for kind, btn in self.icon_buttons.items():
            btn.setIcon(make_icon(kind, fg))

    def _setup_shortcuts(self):
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=self._safe(fn))

        sc("Ctrl+T", lambda: self.new_tab())
        sc("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex()))
        sc("Ctrl+L", lambda: (self.url_bar.setFocus(), self.url_bar.selectAll()))
        sc("Ctrl+R", lambda: self.current().reload())
        sc("F5", lambda: self.current().reload())
        sc("Alt+Left", lambda: self.current().back())
        sc("Alt+Right", lambda: self.current().forward())
        sc("Ctrl+F", self.show_find)
        sc("Esc", self.hide_find)
        sc("Ctrl++", lambda: self.zoom(0.1))
        sc("Ctrl+=", lambda: self.zoom(0.1))
        sc("Ctrl+-", lambda: self.zoom(-0.1))
        sc("Ctrl+0", lambda: self.current().setZoomFactor(1.0))
        sc("Ctrl+Shift+Delete", self.wipe)
        sc("Ctrl+,", self.open_settings)
        sc("Ctrl+Shift+O", self.open_external)

    # ---------- Настройки ----------
    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s = self.settings
        before = (s.search_engine, s.protection, s.remember_logins)
        dlg.apply()
        after = (s.search_engine, s.protection, s.remember_logins)
        self._apply_cookie_policy()
        self._update_autofill_script()
        if before == after:
            return

        # Экран затемняется и просит подождать, пока применяются настройки
        # (переключение поисковика, защиты и т.д.).
        engine_changed = before[0] != after[0]
        self.show_busy("PLEASE WAIT A FEW SECONDS")
        QTimer.singleShot(60, self._safe(lambda: self._apply_settings_changes(engine_changed)))

    def _apply_settings_changes(self, engine_changed=False):
        self._pending_home = set()
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if v.property("home"):
                self._pending_home.add(v)
                self.show_home(v)
            elif engine_changed:
                # Открыта страница результатов старого поисковика — повторяем
                # тот же запрос в новом, иначе кажется, что переключение "не работает".
                q = extract_search_query(v.url())
                if q:
                    self._pending_home.add(v)
                    v.load(QUrl(self.settings.search_url + quote_plus(q)))
        if not self._pending_home:
            QTimer.singleShot(900, self.hide_busy)
        QTimer.singleShot(10000, self.hide_busy)  # страховка: не зависаем навсегда

    def _home_done(self, view):
        if view in self._pending_home:
            self._pending_home.discard(view)
            if not self._pending_home:
                QTimer.singleShot(700, self.hide_busy)  # чтобы текст успели прочитать

    # ---------- Затемнение экрана ----------
    def show_busy(self, text):
        t = THEMES[self.theme]
        veil = "rgba(0, 0, 0, 205)" if self.theme == "dark" else "rgba(255, 255, 255, 215)"
        self.busy.setText(text)
        self.busy.setStyleSheet(
            f"background: {veil}; color: {t['fg']}; font-size: 16px; letter-spacing: 6px;"
            "font-family: Georgia, 'Times New Roman', serif;"
        )
        self.busy.setGeometry(self.rect())
        self.busy.show()
        self.busy.raise_()
        QApplication.processEvents()

    def hide_busy(self):
        self.busy.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "busy"):
            self.busy.setGeometry(self.rect())
        if hasattr(self, "warning_overlay") and self.warning_overlay.isVisible():
            self.warning_overlay.setGeometry(self.rect())

    # ---------- Вкладки ----------
    def current(self) -> QWebEngineView:
        return self.tabs.currentWidget()

    def new_tab(self, url: QUrl | None = None) -> QWebEngineView:
        view = QWebEngineView()
        view.setPage(Page(self.profile, lambda: self.new_tab(), self, view))
        view.setProperty("home", False)

        idx = self.tabs.addTab(view, "NEW TAB")
        self.tabs.setCurrentIndex(idx)
        self._animate_tab_in(view)

        view.titleChanged.connect(self._safe(lambda title, v=view: self._on_title(v, title)))
        view.urlChanged.connect(self._safe(lambda u, v=view: self._on_url(v, u)))
        view.urlChanged.connect(self._safe(lambda u, v=view: self._check_site_risk(v, u)))
        view.urlChanged.connect(self._safe(lambda u, v=view: self._check_external_app(v, u)))
        view.loadProgress.connect(self._safe(lambda p, v=view: self._on_progress(v, p)))
        view.loadFinished.connect(self._safe(lambda ok, v=view: self._home_done(v)))
        view.page().fullScreenRequested.connect(self._safe(self._on_fullscreen))

        if url is not None and not url.isEmpty():
            view.load(url)
        else:
            self.show_home(view)
        return view

    def _make_curtain(self, widget):
        """Непрозрачный виджет-«занавеска» поверх вкладки — QGraphicsOpacityEffect
        на самом QWebEngineView обычно ничего не делает (движок рисует свой слой
        в обход обычного composited-рендеринга Qt), а на простом QWidget
        анимация прозрачности работает всегда."""
        curtain = QWidget(widget)
        t = THEMES[self.theme]
        curtain.setStyleSheet(f"background: {t['bg']};")
        curtain.setGeometry(widget.rect())
        curtain.show()
        curtain.raise_()
        return curtain

    def _animate_tab_in(self, widget):
        """Короткое (180мс) плавное появление новой вкладки: тёмная/светлая
        занавеска гаснет, открывая страницу под ней."""
        curtain = self._make_curtain(widget)
        effect = QGraphicsOpacityEffect(curtain)
        curtain.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", curtain)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(curtain.deleteLater)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        widget._open_anim = anim  # держим ссылку живой на время анимации
        widget._open_curtain = curtain

    def close_tab(self, index):
        if index < 0 or index >= self.tabs.count():
            return
        widget = self.tabs.widget(index)
        if widget is None:
            return

        curtain = self._make_curtain(widget)
        effect = QGraphicsOpacityEffect(curtain)
        curtain.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", curtain)
        anim.setDuration(140)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._safe(lambda w=widget: self._finish_close_tab(w)))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        widget._close_anim = anim  # держим ссылку живой на время анимации
        widget._close_curtain = curtain

    def _finish_close_tab(self, widget):
        index = self.tabs.indexOf(widget)
        if index == -1:
            widget.deleteLater()
            return
        if self.tabs.count() <= 1:
            self.tabs.removeTab(index)
            widget.deleteLater()
            self.new_tab()
            return
        self.tabs.removeTab(index)
        widget.deleteLater()

    def show_home(self, view=None):
        view = view or self.current()
        view.setProperty("home", True)
        view.setHtml(newtab_html(self.theme, self.settings.search_url), QUrl("about:blank"))
        if view is self.current():
            self.url_bar.clear()
            self.url_bar.setFocus()

    def go_home(self):
        self.show_home()

    def navigate(self):
        url = normalize_input(self.url_bar.text(), self.settings.search_url)
        if url.isEmpty():
            return
        view = self.current()
        view.setProperty("home", False)
        view.load(url)
        view.setFocus()

    # ---------- Обработчики ----------
    def _on_title(self, view, title):
        i = self.tabs.indexOf(view)
        if i < 0:
            return
        title = (title or "NEW TAB").upper()
        self.tabs.setTabText(i, title if len(title) <= 18 else title[:17] + "…")
        self.tabs.setTabToolTip(i, title)
        if view is self.current():
            self.setWindowTitle(f"{title} — {APP_NAME}")

    def _on_url(self, view, url: QUrl):
        if url.scheme() in ("http", "https"):
            view.setProperty("home", False)
        if view is self.current():
            if view.property("home") or url.scheme() in ("about", "data"):
                self.url_bar.clear()
            else:
                self.url_bar.setText(url.toString())
                self.url_bar.setCursorPosition(0)

    def _check_site_risk(self, view, url: QUrl):
        if view is self.current():
            self.warning_overlay.hide()
        if url.scheme() not in ("http", "https"):
            return
        host = url.host().lower()
        if not host or host in self._risk_allowed:
            return
        reasons = assess_site_risk(url)
        if reasons and view is self.current():
            self.warning_overlay.show_for(view, url, reasons)

    def _check_external_app(self, view, url: QUrl):
        if view is not self.current():
            return
        host = url.host().lower()
        for matches, app_name, converter in EXTERNAL_LINK_RULES:
            if matches(host):
                try:
                    deep_link = converter(url)
                except Exception:
                    deep_link = None
                if deep_link:
                    self._show_app_banner(app_name, deep_link)
                    return
        self.app_banner.hide()

    def offer_external_open(self, url: QUrl):
        """Вызывается из Page.acceptNavigationRequest для схем вида mailto:/tg:/skype:
        которые движок не умеет загрузить как обычную страницу."""
        app_name = SCHEME_APP_NAMES.get(url.scheme(), url.scheme().capitalize())
        self._show_app_banner(app_name, url.toString())

    def _show_app_banner(self, app_name: str, deep_link: str):
        self._pending_deep_link = deep_link
        self.app_banner_label.setText(f"This link can be opened in the {app_name} app")
        self.app_banner.show()

    def _open_external_app(self):
        if self._pending_deep_link:
            QDesktopServices.openUrl(QUrl(self._pending_deep_link))
        self.app_banner.hide()

    def _on_progress(self, view, p):
        if view is self.current():
            self.progress.setValue(0 if p >= 100 else p)

    def _on_tab_changed(self, _):
        v = self.current()
        if v is None:
            return
        u = v.url()
        if v.property("home") or u.scheme() in ("about", "data") or u.isEmpty():
            self.url_bar.clear()
        else:
            self.url_bar.setText(u.toString())
        self.setWindowTitle(f"{v.title() or 'New tab'} — {APP_NAME}")
        self.app_banner.hide()
        self._check_external_app(v, u)

    def _on_blocked(self):
        self.blocked_count += 1
        self.shield.setText(f"● {self.blocked_count} BLOCKED")

    def _on_fullscreen(self, request):
        request.accept()
        if request.toggleOn():
            self.showFullScreen()
        else:
            self.showNormal()

    def _on_download(self, download):
        name = download.downloadFileName() or "download"
        downloads_dir = (
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
            or DATA_DIR
        )
        try:
            os.makedirs(downloads_dir, exist_ok=True)
        except Exception:
            pass
        path = unique_path(os.path.join(downloads_dir, name))
        folder, fname = os.path.split(path)
        download.setDownloadDirectory(folder)
        download.setDownloadFileName(fname)
        download.accept()
        self._register_download(download, fname)

    def _register_download(self, download, filename):
        row = self.downloads_panel.add_row(filename)
        self._active_downloads += 1
        self._start_download_pulse()

        download.receivedBytesChanged.connect(
            self._safe(lambda d=download, r=row: self._update_download_progress(d, r))
        )
        download.isFinishedChanged.connect(
            self._safe(lambda d=download, r=row: self._download_finished(d, r))
        )

    def _update_download_progress(self, download, row):
        total = download.totalBytes()
        received = download.receivedBytes()
        if total and total > 0:
            pct = int(received * 100 / max(total, 1))
            row.bar.setRange(0, 100)
            row.bar.setValue(pct)
            row.status.setText(f"{human_size(max(total - received, 0))} left")
        else:
            row.bar.setRange(0, 0)  # неизвестный размер — индикатор без деления
            row.status.setText(f"{human_size(received)} downloaded")

    def _download_finished(self, download, row):
        row.bar.setRange(0, 100)
        row.bar.setValue(100)
        try:
            state = download.state()
            completed = state == download.DownloadState.DownloadCompleted
        except Exception:
            completed = True
        row.status.setText("Done" if completed else "Failed")
        self._active_downloads = max(0, self._active_downloads - 1)
        if self._active_downloads == 0:
            self._stop_download_pulse()

    def toggle_downloads_panel(self):
        self.downloads_panel.show_below(self.btn_downloads)

    def _start_download_pulse(self):
        """Значок загрузок мягко пульсирует, пока что-то качается."""
        if self._pulse_anim is not None:
            return
        effect = QGraphicsOpacityEffect(self.btn_downloads)
        self.btn_downloads.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self.btn_downloads)
        anim.setDuration(700)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.35)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        anim.start()
        self._pulse_anim = anim

    def _stop_download_pulse(self):
        if self._pulse_anim is not None:
            self._pulse_anim.stop()
            self._pulse_anim = None
        try:
            self.btn_downloads.setGraphicsEffect(None)
        except Exception:
            pass

    # ---------- Утилиты ----------
    def zoom(self, delta):
        v = self.current()
        v.setZoomFactor(max(0.25, min(5.0, v.zoomFactor() + delta)))

    def show_find(self):
        self.find_bar.show()
        self.find_bar.setFocus()
        self.find_bar.selectAll()

    def hide_find(self):
        if self.find_bar.isVisible():
            self.find_bar.hide()
            self.current().findText("")

    def wipe(self):
        self.profile.clearHttpCache()
        if not self.settings.remember_logins:
            self.profile.cookieStore().deleteAllCookies()
        self.profile.clearAllVisitedLinks()
        self.blocked_count = 0
        self.shield.setText("● 0 BLOCKED")
        try:
            from PyQt6.QtWidgets import QToolTip
            QToolTip.showText(self.btn_wipe.mapToGlobal(self.btn_wipe.rect().center()), "Wiped ✓")
        except Exception:
            pass

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.apply_theme()
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if v.property("home"):
                self.show_home(v)
        try:
            from PyQt6.QtWidgets import QToolTip
            label = "Dark ✓" if self.theme == "dark" else "Light ✓"
            QToolTip.showText(self.btn_theme.mapToGlobal(self.btn_theme.rect().center()), label)
        except Exception:
            pass

    def _apply_web_color_scheme(self):
        """Сайты (Google и др.) следуют теме браузера через prefers-color-scheme.
        Реально работает только на Qt 6.8+. На более старых версиях сайты не
        трогаем: принудительная инверсия цветов Chromium (ForceDarkMode) часто
        выглядит криво/сломано на сайтах с картинками — это было хуже, чем
        просто оставить сайт как есть."""
        dark = self.theme == "dark"
        try:
            hints = QGuiApplication.styleHints()
            if hasattr(hints, "setColorScheme"):  # Qt 6.8+
                hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
                QTimer.singleShot(150, self._reload_web_tabs)
        except Exception:
            traceback.print_exc()

    def _reload_web_tabs(self):
        """Перезагружает открытые сайты, чтобы они подхватили новую тему."""
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if v is not None and not v.property("home") and v.url().scheme() in ("http", "https"):
                v.reload()

    def open_external(self):
        """Ctrl+Shift+O — открыть текущую страницу в обычном браузере системы."""
        url = self.current().url()
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def apply_theme(self):
        self._apply_web_color_scheme()
        self.setStyleSheet(stylesheet(self.theme))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        self._refresh_icons()

    def closeEvent(self, event):
        try:
            self.wipe()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    install_crash_handler()
    print("Qt:", qVersion())
    print(
        "Website dark/light follows browser theme:",
        hasattr(QGuiApplication.styleHints(), "setColorScheme"),
    )
    app.setApplicationName(APP_NAME)
    win = Browser()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
