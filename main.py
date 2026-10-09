import os
import json
import time
import math
import sqlite3
import threading
import urllib.parse
import urllib.request

from datetime import datetime, timedelta
from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.utils import get_color_from_hex
from kivy.uix.boxlayout import BoxLayout
from kivy.properties import StringProperty, ListProperty

DB_FILE = os.path.join(
    os.path.dirname(__file__), "market_data.db"
)

GROWW_URL = (
    "https://groww.in/v1/api/option_chain_service/"
    "v1/option_chain/derivatives/nifty"
)

EXPIRY1 = "2026-10-13"
EXPIRY2 = "2026-10-27"
STRIKE1 = 22500
STRIKE2 = 22500
REFRESH_SECONDS = 1

BG = "#17191E"
PANEL = "#22252D"
WHITE = "#F2F4F8"
GREEN = "#00D68F"
BLUE = "#43A5FF"
RED = "#FF5C70"
YELLOW = "#FFD166"
MUTED = "#9AA4B2"

KV = r"""
<MonitorRoot>:
    orientation: "vertical"
    padding: "10dp"
    spacing: "8dp"

    canvas.before:
        Color:
            rgba: app.bg_color
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        size_hint_y: None
        height: "48dp"
        canvas.before:
            Color:
                rgba: app.panel_color
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [10]
        Label:
            text: "[b]NIFTY LIVE MONITOR[/b]"
            markup: True
            color: app.blue_color
            font_size: "20sp"

    BoxLayout:
        size_hint_y: None
        height: "56dp"
        spacing: "8dp"

        BoxLayout:
            orientation: "vertical"
            canvas.before:
                Color:
                    rgba: app.panel_color
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [8]
            Label:
                text: "FUT2 LIVE"
                color: app.green_color
                font_size: "13sp"
            Label:
                text: root.fut2_text
                color: app.white_color
                font_size: "20sp"
                bold: True

        BoxLayout:
            orientation: "vertical"
            canvas.before:
                Color:
                    rgba: app.panel_color
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [8]
            Label:
                text: "FUT1 × RATIO"
                color: app.blue_color
                font_size: "13sp"
            Label:
                text: root.scaled_text
                color: app.white_color
                font_size: "20sp"
                bold: True

    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.panel_color
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [8]

        BoxLayout:
            size_hint_y: None
            height: "34dp"
            padding: "8dp", 0
            Label:
                text: "7-DAY COMPARISON CHART"
                color: app.white_color
                bold: True
                halign: "left"
            Label:
                text: root.point_text
                color: app.muted_color
                font_size: "11sp"

        Widget:
            id: chart

    GridLayout:
        cols: 2
        size_hint_y: None
        height: "90dp"
        spacing: "8dp"

        BoxLayout:
            orientation: "vertical"
            padding: "8dp"
            canvas.before:
                Color:
                    rgba: app.panel_color
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [8]
            Label:
                text: "NIFTY SPOT"
                color: app.muted_color
            Label:
                text: root.spot_text
                color: app.white_color
                bold: True

        BoxLayout:
            orientation: "vertical"
            padding: "8dp"
            canvas.before:
                Color:
                    rgba: app.panel_color
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [8]
            Label:
                text: "DIFFERENCE"
                color: app.yellow_color
            Label:
                text: root.diff_text
                color: app.white_color
                bold: True

    Label:
        size_hint_y: None
        height: "24dp"
        text: root.status_text
        color: app.muted_color
        font_size: "12sp"

    BoxLayout:
        size_hint_y: None
        height: "28dp"
        spacing: "12dp"
        Label:
            text: "● FUT2 LIVE"
            color: app.green_color
            font_size: "12sp"
        Label:
            text: "● FUT1 × RATIO"
            color: app.blue_color
            font_size: "12sp"

    Button:
        text: "REFRESH NOW"
        size_hint_y: None
        height: "44dp"
        background_normal: ""
        background_color: app.blue_color
        color: 1, 1, 1, 1
        on_release: root.request_update()
"""

class MonitorRoot(BoxLayout):
    fut2_text = StringProperty("N/A")
    scaled_text = StringProperty("N/A")
    spot_text = StringProperty("N/A")
    diff_text = StringProperty("N/A")
    status_text = StringProperty("Starting...")
    point_text = StringProperty("0 points")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db = None
        self.busy = False
        self.last_values = None
        self.history = {"fut2": [], "scaled": []}

    def start(self):
        self.db = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                ts REAL PRIMARY KEY,
                spot REAL,
                fut1 REAL,
                fut2 REAL,
                ratio REAL,
                scaled REAL
            )
        """)
        self.db.commit()
        self.cleanup_old_data()
        self.load_history()
        self.draw_chart()
        self.request_update()
        Clock.schedule_interval(
            lambda dt: self.request_update(),
            REFRESH_SECONDS
        )

    def cleanup_old_data(self):
        cutoff = time.time() - 7 * 24 * 60 * 60
        self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        self.db.commit()

    def load_history(self):
        cutoff = time.time() - 7 * 24 * 60 * 60
        rows = self.db.execute(
            "SELECT ts, fut2, scaled FROM samples "
            "WHERE ts >= ? ORDER BY ts",
            (cutoff,)
        ).fetchall()
        self.history = {
            "fut2": [(r[0], r[1]) for r in rows if r[1] is not None],
            "scaled": [(r[0], r[2]) for r in rows if r[2] is not None],
        }
        self.point_text = f"{len(rows)} saved points"

    def fetch_json(self, expiry):
        params = urllib.parse.urlencode({
            "expiry": expiry,
            "_": int(time.time() * 1000)
        })
        req = urllib.request.Request(
            GROWW_URL + "?" + params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://groww.in/"
            }
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def extract_chain(data):
        result = {}
        try:
            for item in data["optionChain"]["optionChains"]:
                strike = int(round(float(item["strikePrice"]) / 100))
                call = item.get("callOption") or {}
                put = item.get("putOption") or {}
                c = call.get("ltp")
                p = put.get("ltp")
                result[strike] = (
                    float(c) if c is not None else None,
                    float(p) if p is not None else None
                )
        except (KeyError, TypeError, ValueError):
            pass
        return result

    @staticmethod
    def extract_spot(data):
        try:
            return float(data["livePrice"]["value"])
        except (KeyError, TypeError, ValueError):
            return None

    def request_update(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self.fetch_worker, daemon=True).start()

    def fetch_worker(self):
        try:
            d1 = self.fetch_json(EXPIRY1)
            d2 = self.fetch_json(EXPIRY2)

            c1, p1 = self.extract_chain(d1).get(STRIKE1, (None, None))
            c2, p2 = self.extract_chain(d2).get(STRIKE2, (None, None))

            fut1 = STRIKE1 + c1 - p1 if c1 is not None and p1 is not None else None
            fut2 = STRIKE2 + c2 - p2 if c2 is not None and p2 is not None else None
            spot = self.extract_spot(d1)

            Clock.schedule_once(
                lambda dt: self.apply_data(spot, fut1, fut2), 0
            )
        except Exception as exc:
            message = str(exc)[:100]
            Clock.schedule_once(
                lambda dt, msg=message: self.set_error(msg), 0
            )

    def set_error(self, message):
        self.status_text = "API error: " + message
        self.busy = False

    def apply_data(self, spot, fut1, fut2):
        try:
            ratio = None
            scaled = None
            if fut1 is not None and fut2 is not None:
                row = self.db.execute(
                    "SELECT fut1, fut2 FROM samples "
                    "WHERE fut1 IS NOT NULL AND fut2 IS NOT NULL "
                    "ORDER BY ts LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    ratio = row[1] / row[0]

            if ratio is not None and fut1 is not None:
                scaled = fut1 * ratio

            now = time.time()
            self.db.execute(
                "INSERT OR REPLACE INTO samples "
                "(ts, spot, fut1, fut2, ratio, scaled) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, spot, fut1, fut2, ratio, scaled)
            )
            self.db.commit()
            self.cleanup_old_data()

            if spot is not None:
                self.spot_text = f"{spot:,.2f}"
            if fut1 is not None:
                self.scaled_text = (
                    f"{scaled:,.2f}" if scaled is not None else "Waiting for reference"
                )
            if fut2 is not None:
                self.fut2_text = f"{fut2:,.2f}"

            if fut2 is not None and scaled is not None:
                self.diff_text = f"{fut2 - scaled:+,.2f}"

            self.status_text = (
                "Updated " + datetime.now().strftime("%H:%M:%S")
                + " | 7-day local history"
            )
            self.load_history()
            self.draw_chart()
        finally:
            self.busy = False

    def draw_chart(self):
        chart = self.ids.get("chart")
        if chart is None:
            return
        chart.canvas.clear()

        from kivy.graphics import Color, Line, Rectangle
        from kivy.core.text import Label as CoreLabel

        w, h = chart.size
        x0, y0 = 42, 25
        cw, ch = max(1, w - 55), max(1, h - 45)

        with chart.canvas:
            Color(0.25, 0.28, 0.33, 1)
            for i in range(5):
                y = y0 + ch * i / 4
                Line(points=[x0, y, x0 + cw, y], width=0.7)

            all_values = [
                v for series in self.history.values()
                for _, v in series if v is not None
            ]
            if len(all_values) < 2:
                return

            low, high = min(all_values), max(all_values)
            if math.isclose(low, high):
                low -= 1
                high += 1

            cutoff = time.time() - 7 * 24 * 60 * 60

            for key, color in (
                ("fut2", (0, 0.84, 0.56, 1)),
                ("scaled", (0.26, 0.65, 1, 1))
            ):
                points = []
                series = self.history[key]
                for ts, value in series:
                    if value is None:
                        continue
                    x = x0 + ((ts - cutoff) / (7 * 24 * 60 * 60)) * cw
                    y = y0 + ((value - low) / (high - low)) * ch
                    points.extend([x, y])
                if len(points) >= 4:
                    Color(*color)
                    Line(points=points, width=1.5)

    def on_size(self, *args):
        if hasattr(self, "history"):
            Clock.schedule_once(lambda dt: self.draw_chart(), 0)

class NiftyApp(App):
    bg_color = ListProperty(get_color_from_hex(BG))
    panel_color = ListProperty(get_color_from_hex(PANEL))
    white_color = ListProperty(get_color_from_hex(WHITE))
    green_color = ListProperty(get_color_from_hex(GREEN))
    blue_color = ListProperty(get_color_from_hex(BLUE))
    yellow_color = ListProperty(get_color_from_hex(YELLOW))
    muted_color = ListProperty(get_color_from_hex(MUTED))

    def build(self):
        Builder.load_string(KV)
        self.root_widget = MonitorRoot()
        return self.root_widget

    def on_start(self):
        self.root_widget.start()

if __name__ == "__main__":
    NiftyApp().run()
